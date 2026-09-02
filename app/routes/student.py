"""
routes/student.py — Student Blueprint
=======================================
Covers: student portal, registration, login, OTP flows, face enrollment,
        subject enrollment/unenrollment, attendance data fetch.

Blueprint name: 'student'
url_for prefix: url_for('student.student_portal'), etc.
"""

import os
import uuid
import traceback
import logging
import json
from datetime import datetime, timedelta, timezone
from flask import Blueprint, render_template, request, redirect, url_for, flash, session, jsonify
from flask_login import login_required, login_user, logout_user, current_user
import cv2
import numpy as np
import face_recognition
from sqlalchemy import desc, func
from werkzeug.utils import secure_filename
from app.extensions import db, limiter
from app.models import Student, Subject, SubjectStaff, AttendanceRecord, BoundingBox, Photo, AttendanceStatus
from app.utils.attendance_review import (
    IDENTIFICATION_TYPE_USER_ADDED,
    IDENTIFICATION_TYPE_USER_DELETED,
    REVIEW_STATUS_PENDING,
    attendance_source_label,
    build_box_payload,
    group_boxes_by_request_group,
    infer_attendance_source,
    photo_backed_attendance_dates,
    sanitize_bounding_box,
)
from app.utils.decorators import require_student
from app.utils.email import send_email
from app.utils.face import face_executor
from app.utils.security import generate_otp, otp_matches

logger = logging.getLogger(__name__)
student_bp = Blueprint('student', __name__)

# ── Directory constants are resolved lazily via current_app ─────────────────
def _enrollment_dir():
    from flask import current_app
    d = os.path.join(current_app.static_folder, 'enrollment_uploads')
    os.makedirs(d, exist_ok=True)
    return d


def _normalize_roll_input(raw_roll):
    return (raw_roll or '').strip().upper()


def _normalize_roll_for_compare(raw_roll):
    return _normalize_roll_input(raw_roll)


def _find_student_by_roll(raw_roll):
    normalized_roll = _normalize_roll_input(raw_roll)
    if not normalized_roll:
        return None, normalized_roll

    student = db.session.get(Student, normalized_roll)
    if student:
        return student, normalized_roll

    student = Student.query.filter(func.upper(Student.roll_number) == normalized_roll).first()
    if student:
        return student, normalized_roll

    student = Student.query.filter(func.upper(func.trim(Student.roll_number)) == normalized_roll).first()
    return student, normalized_roll


def _canonical_current_student_roll():
    raw_roll = getattr(current_user, 'roll_number', None)
    student, _ = _find_student_by_roll(raw_roll)
    if student and student.roll_number:
        return student.roll_number
    return (raw_roll or '').strip()


def _verified_student_or_redirect(message):
    student = db.session.get(Student, current_user.roll_number)
    if not student:
        logout_user()
        flash('Student not found.', 'danger')
        return redirect(url_for('student.student_portal'))
    if not student.is_verified:
        flash(message, 'warning')
        return redirect(url_for('student.student_portal'))
    return None


def _verified_student_or_json(message):
    student = db.session.get(Student, current_user.roll_number)
    if not student:
        logout_user()
        return jsonify({'error': 'Student not found.'}), 404
    if not student.is_verified:
        return jsonify({'error': message}), 403
    return None


def _parse_json_list(raw_value, field_name):
    if raw_value in (None, ''):
        return []
    try:
        parsed = json.loads(raw_value)
    except json.JSONDecodeError:
        raise ValueError(f'Invalid {field_name} payload.')
    if not isinstance(parsed, list):
        raise ValueError(f'{field_name} must be a list.')
    return parsed


def _submit_student_box_changes(photo, student_roll, remove_box_ids, add_box_inputs):
    pending_request = BoundingBox.query.filter(
        BoundingBox.photo_id == photo.id,
        BoundingBox.student_roll_number == student_roll,
        BoundingBox.review_status == REVIEW_STATUS_PENDING,
        BoundingBox.identification_type.in_([IDENTIFICATION_TYPE_USER_ADDED, IDENTIFICATION_TYPE_USER_DELETED])
    ).first()
    if pending_request:
        raise ValueError('You already have a pending review request for this photo.')

    normalized_remove_ids = []
    seen_remove_ids = set()
    for raw_id in remove_box_ids or []:
        try:
            box_id = int(raw_id)
        except (TypeError, ValueError):
            raise ValueError('Invalid delete selection.')
        if box_id not in seen_remove_ids:
            seen_remove_ids.add(box_id)
            normalized_remove_ids.append(box_id)

    source_boxes = []
    if normalized_remove_ids:
        source_boxes = BoundingBox.query.filter(
            BoundingBox.id.in_(normalized_remove_ids),
            BoundingBox.photo_id == photo.id,
            BoundingBox.student_roll_number == student_roll,
            BoundingBox.is_active == True,
            BoundingBox.is_approved == True
        ).order_by(BoundingBox.id).all()
        source_box_ids = {box.id for box in source_boxes}
        if len(source_boxes) != len(normalized_remove_ids) or source_box_ids != set(normalized_remove_ids):
            raise ValueError('Only your active approved bounding boxes can be removed.')

    sanitized_add_boxes = []
    for raw_bbox in add_box_inputs or []:
        bbox = sanitize_bounding_box(raw_bbox, photo.image_width, photo.image_height)
        if not bbox:
            raise ValueError('One or more bounding boxes are invalid.')
        sanitized_add_boxes.append(bbox)

    if not source_boxes and not sanitized_add_boxes:
        raise ValueError('Add at least one new box or remove an existing one before submitting.')

    request_group_id = uuid.uuid4().hex
    for source_box in source_boxes:
        db.session.add(BoundingBox(
            photo_id=photo.id,
            student_roll_number=student_roll,
            bounding_box=source_box.bounding_box,
            identification_type=IDENTIFICATION_TYPE_USER_DELETED,
            is_approved=False,
            review_status=REVIEW_STATUS_PENDING,
            is_active=True,
            created_by_student_roll_number=student_roll,
            request_group_id=request_group_id,
            source_box_id=source_box.id
        ))

    for bbox in sanitized_add_boxes:
        db.session.add(BoundingBox(
            photo_id=photo.id,
            student_roll_number=student_roll,
            bounding_box=bbox,
            identification_type=IDENTIFICATION_TYPE_USER_ADDED,
            is_approved=False,
            review_status=REVIEW_STATUS_PENDING,
            is_active=True,
            created_by_student_roll_number=student_roll,
            request_group_id=request_group_id
        ))

    return {
        'request_group_id': request_group_id,
        'remove_count': len(source_boxes),
        'add_count': len(sanitized_add_boxes),
    }


# ── Routes ───────────────────────────────────────────────────────────────────
@student_bp.route("/student-portal")
def student_portal():
    enrolled_subjects = []
    available_subjects = []
    if current_user.is_authenticated and isinstance(current_user, Student) and current_user.is_verified:
        enrolled_subjects = current_user.subjects.order_by(Subject.name).all()
        enrolled_subject_ids = [s.id for s in enrolled_subjects]
        available_subjects = Subject.query.filter(
            db.not_(Subject.id.in_(enrolled_subject_ids)),
            Subject.archived == False
        ).order_by(Subject.name).all()
    return render_template('student-portal.html',
                           enrolled_subjects=enrolled_subjects,
                           available_subjects=available_subjects)


@student_bp.route("/student-register", methods=['POST'])
@limiter.limit("5 per hour")
def student_register():
    try:
        roll = _normalize_roll_input(request.form.get('roll_number'))
        name = request.form.get('name')
        email = request.form.get('email')
        pwd = request.form.get('password')
        if not all([roll, name, email, pwd]):
            return jsonify({'error': 'All fields required.'}), 400
        if len(pwd) < 6:
            return jsonify({'error': 'Password too short.'}), 400
        if not email.lower().endswith("@iitj.ac.in"):
            return jsonify({'error': 'Only @iitj.ac.in emails are allowed.'}), 400
        existing_student, _ = _find_student_by_roll(roll)
        if existing_student:
            return jsonify({'error': 'Roll Number exists.'}), 409
        if Student.query.filter_by(email=email).first():
            return jsonify({'error': 'Email exists.'}), 409

        otp = generate_otp()
        student = Student(roll_number=roll, name=name, email=email,
                          otp=otp, otp_generated_at=datetime.now(timezone.utc))
        student.set_password(pwd)
        db.session.add(student)

        if not send_email(student.email, "Verify Email",
                          f"Hi {name},\nCode: {otp}\nExpires in 10 mins."):
            db.session.rollback()
            return jsonify({'error': 'Failed to send verification email.'}), 500
        db.session.commit()
        return jsonify({'message': f'Registered! Code sent to {student.email}.'})
    except Exception as e:
        db.session.rollback()
        msg = 'Roll Number or Email already exists.' if "violates unique constraint" in str(e).lower() else f'DB error: {e}'
        return jsonify({'error': msg}), 500


@student_bp.route("/student-login", methods=['POST'])
@limiter.limit("5 per minute")
def student_login():
    try:
        roll = _normalize_roll_input(request.form.get('roll_number'))
        pwd = request.form.get('password')
        if not all([roll, pwd]):
            return jsonify({'error': 'Roll/Password required.'}), 400
        student, _ = _find_student_by_roll(roll)
        if not student:
            return jsonify({'error': 'Roll Number not found.'}), 404
        if not student.password_hash:
            return jsonify({'error': 'Password not set. Use Forgot Password.'}), 401
        if not student.check_password(pwd):
            return jsonify({'error': 'Invalid password.'}), 401
        if not student.is_verified:
            return jsonify({'error': 'Complete OTP verification and face enrollment before logging in.'}), 403
        login_user(student)
        session['user_type'] = 'student'
        session.pop('update_verified', None)
        return jsonify({'message': 'Login successful!', 'reload': True})
    except Exception as e:
        return jsonify({'error': f'Internal error: {e}'}), 500


@student_bp.route("/verify-otp", methods=['POST'])
@limiter.limit("5 per 10 minutes")
def verify_otp():
    try:
        roll = _normalize_roll_input(request.form.get('roll_number'))
        otp_attempt = request.form.get('otp')
        if not all([roll, otp_attempt]):
            return jsonify({'error': 'Roll/OTP required.'}), 400
        student, _ = _find_student_by_roll(roll)
        if not student:
            return jsonify({'error': 'Student not found.'}), 404
        if student.otp is None:
            return jsonify({'error': 'No pending/expired OTP.'}), 400
        if not otp_matches(student.otp, otp_attempt):
            return jsonify({'error': 'Invalid OTP.'}), 400
        if student.otp_generated_at is None or (datetime.now(timezone.utc) - student.otp_generated_at) > timedelta(minutes=10):
            student.otp = None; student.otp_generated_at = None; db.session.commit()
            return jsonify({'error': 'OTP expired.'}), 400
        login_user(student)
        session['user_type'] = 'student'
        session['update_verified'] = True
        student.otp = None; student.otp_generated_at = None
        db.session.commit()
        return jsonify({'message': 'Verified! Proceed with face enrollment.', 'is_verified': student.is_verified})
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': f'Internal error: {e}'}), 500


@student_bp.route("/resend-otp", methods=['POST'])
@limiter.limit("3 per 10 minutes")
def resend_otp():
    try:
        roll = _normalize_roll_input(request.form.get('roll_number'))
        context = request.form.get('context')
        if not roll or not context:
            return jsonify({'error': 'Roll Number and context required.'}), 400
        student, _ = _find_student_by_roll(roll)
        if not student:
            return jsonify({'error': 'Student not found.'}), 404
        new_otp = generate_otp()
        if context == 'register':
            subj = "Verify Email (Resend)"
            body = f"Hi {student.name},\nYour new verification code is: {new_otp}\nExpires in 10 minutes."
        elif context == 'update':
            subj = "Profile Update Code (Resend)"
            body = f"Hi {student.name},\nYour new profile update code is: {new_otp}\nExpires in 10 minutes."
        else:
            return jsonify({'error': 'Invalid OTP context.'}), 400
        student.otp = new_otp
        student.otp_generated_at = datetime.now(timezone.utc)
        if not send_email(student.email, subj, body):
            db.session.rollback()
            return jsonify({'error': 'Failed to send new OTP email.'}), 500
        db.session.commit()
        return jsonify({'message': f'New OTP sent to {student.email}.'})
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': f'Internal error: {e}'}), 500


@student_bp.route("/request-password-reset-otp", methods=['POST'])
@limiter.limit("3 per 10 minutes")
def request_password_reset_otp():
    try:
        roll = _normalize_roll_input(request.form.get('roll_number'))
        if not roll:
            return jsonify({'error': 'Roll Number required.'}), 400
        student, _ = _find_student_by_roll(roll)
        if not student:
            return jsonify({'message': 'If account exists, code sent.'})
        otp = generate_otp()
        student.otp = otp
        student.otp_generated_at = datetime.now(timezone.utc)
        if not send_email(student.email, "Password Reset Code",
                          f"Hi {student.name},\nCode: {otp}\nExpires in 10 mins."):
            db.session.rollback()
            return jsonify({'error': 'Failed to send code.'}), 500
        db.session.commit()
        return jsonify({'message': f'Reset code sent to {student.email}.'})
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': 'Internal error.'}), 500


@student_bp.route("/reset-password-with-otp", methods=['POST'])
@limiter.limit("5 per 10 minutes")
def reset_password_with_otp():
    try:
        roll = _normalize_roll_input(request.form.get('roll_number'))
        otp_attempt = request.form.get('otp')
        new_pwd = request.form.get('new_password')
        if not all([roll, otp_attempt, new_pwd]):
            return jsonify({'error': 'All fields required.'}), 400
        if len(new_pwd) < 6:
            return jsonify({'error': 'Password too short.'}), 400
        student, _ = _find_student_by_roll(roll)
        if not student or student.otp is None:
            return jsonify({'error': 'Invalid Roll/OTP.'}), 400
        if not otp_matches(student.otp, otp_attempt):
            return jsonify({'error': 'Invalid OTP.'}), 400
        if student.otp_generated_at is None or (datetime.now(timezone.utc) - student.otp_generated_at) > timedelta(minutes=10):
            student.otp = None; student.otp_generated_at = None; db.session.commit()
            return jsonify({'error': 'OTP expired.'}), 400
        student.set_password(new_pwd); student.otp = None; student.otp_generated_at = None
        db.session.commit()
        return jsonify({'message': 'Password reset! Log in with new password.'})
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': 'Internal error.'}), 500


@student_bp.route("/request-update-otp", methods=['POST'])
@limiter.limit("3 per 10 minutes")
@login_required
@require_student
def request_update_otp():
    try:
        student = db.session.get(Student, current_user.roll_number)
        if not student:
            logout_user()
            return jsonify({'error': 'Student not found.'}), 404
        otp = generate_otp()
        if send_email(student.email, "Profile Update Code",
                      f"Hi {student.name},\nYour profile update code is: {otp}\nExpires in 10 minutes."):
            student.otp = otp
            student.otp_generated_at = datetime.now(timezone.utc)
            db.session.commit()
            return jsonify({'message': f'Code sent to {student.email}.'})
        else:
            return jsonify({'error': 'Failed to send OTP email.'}), 500
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': 'An internal server error occurred.'}), 500


@student_bp.route("/verify-update-otp", methods=['POST'])
@limiter.limit("5 per 10 minutes")
@login_required
@require_student
def verify_update_otp():
    try:
        otp_attempt = request.form.get('otp')
        if not otp_attempt:
            return jsonify({'error': 'OTP required.'}), 400
        student = db.session.get(Student, current_user.roll_number)
        if not student:
            logout_user(); return jsonify({'error': 'Student not found.'}), 404
        if student.otp is None:
            return jsonify({'error': 'Invalid/expired OTP.'}), 400
        if not otp_matches(student.otp, otp_attempt):
            return jsonify({'error': 'Invalid OTP.'}), 400
        if student.otp_generated_at is None or (datetime.now(timezone.utc) - student.otp_generated_at) > timedelta(minutes=10):
            student.otp = None; student.otp_generated_at = None; db.session.commit()
            return jsonify({'error': 'OTP expired.'}), 400
        student.otp = None; student.otp_generated_at = None
        session['update_verified'] = True
        db.session.commit()
        return jsonify({'message': 'Verified! Proceed with update.'})
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': 'Internal error.'}), 500


@student_bp.route("/get-student-details")
@login_required
@require_student
def get_student_details():
    student = db.session.get(Student, current_user.roll_number)
    if not student:
        logout_user(); return jsonify({'error': 'Student not found.'}), 404
    return jsonify({'name': student.name, 'roll_number': student.roll_number,
                    'email': student.email, 'is_verified': student.is_verified})


@student_bp.route("/student/upload-profile-photo", methods=['POST'])
@login_required
@require_student
def upload_profile_photo():
    student = db.session.get(Student, current_user.roll_number)
    if not student:
        logout_user()
        return jsonify({'error': 'Student not found.'}), 404

    if 'profile_photo' not in request.files:
        return jsonify({'error': 'No file part'}), 400

    file = request.files['profile_photo']
    if file.filename == '':
        return jsonify({'error': 'No selected file'}), 400

    if not file.filename.lower().endswith(('.png', '.jpg', '.jpeg')):
        return jsonify({'error': 'Invalid file type. Only JPG and PNG are supported.'}), 400

    secure_name = secure_filename(file.filename)
    unique_filename = f"{uuid.uuid4().hex}_{secure_name}"

    upload_dir = _enrollment_dir()
    file_path = os.path.join(upload_dir, unique_filename)
    try:
        file.save(file_path)
    except Exception as e:
        logger.error(f"Error saving student profile photo: {e}")
        return jsonify({'error': 'Failed to save photo'}), 500

    student.profile_photo_path = url_for('static', filename=f'enrollment_uploads/{unique_filename}')
    db.session.commit()

    return jsonify({
        'message': 'Profile photo updated successfully',
        'profile_photo_url': student.profile_photo_url
    })


@student_bp.route("/enroll", methods=['POST'])
@login_required
@require_student
def enroll_student_face():
    if session.get('update_verified') != True:
        return jsonify({'error': 'Unauthorized. Verify with OTP first.'}), 401

    roll = current_user.get_id()
    student = db.session.get(Student, roll)
    if not student: logout_user(); return jsonify({'error': 'Student not found.'}), 404

    file = request.files.get('video')
    if not file or file.filename == '':
        return jsonify({'error': 'No file selected.'}), 400

    file_bytes = file.read()
    mimetype = file.mimetype
    encodings = []

    try:
        _, extension = os.path.splitext(file.filename)
        secure_fname = f"{uuid.uuid4()}{extension}"
        save_path = os.path.join(_enrollment_dir(), secure_fname)

        with open(save_path, 'wb') as f:
            f.write(file_bytes)

        web_path = url_for('static', filename=f'enrollment_uploads/{secure_fname}')

        if mimetype.startswith('image'):
            nparr = np.frombuffer(file_bytes, np.uint8)
            img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
            if img is None: raise ValueError("Could not decode image.")
            rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
            locs = face_recognition.face_locations(rgb, model="hog")
            if len(locs) != 1:
                return jsonify({'error': f'Found {len(locs)} faces. Upload a clear photo with exactly one face.'}), 400
            
            # Submit encoding extraction to background thread
            future = face_executor.submit(face_recognition.face_encodings, rgb, locs)
            f_encs = future.result()
            encodings.append(f_encs[0].tolist())

            MIN_ENCODINGS = 1

        elif mimetype.startswith('video'):
            vid_cap = cv2.VideoCapture(save_path)
            if not vid_cap.isOpened(): raise Exception(f"Cannot open video: {save_path}")
            frame_count = 0; processed_count = 0
            thumb_saved = False
            while vid_cap.isOpened() and processed_count < 100:
                ret, frame = vid_cap.read()
                if not ret: break
                if frame_count % 5 == 0:
                    processed_count += 1
                    try:
                        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                        locs = face_recognition.face_locations(rgb, model="hog")
                        if locs and len(locs) == 1:
                            if not thumb_saved:
                                thumb_fname = secure_fname.rsplit('.', 1)[0] + '_thumb.jpg'
                                thumb_path = os.path.join(_enrollment_dir(), thumb_fname)
                                cv2.imwrite(thumb_path, frame)
                                thumb_saved = True
                            
                            future = face_executor.submit(face_recognition.face_encodings, rgb, locs)
                            f_encs = future.result()
                            encodings.append(f_encs[0].tolist())
                    except Exception as fe: print(f"Frame {frame_count} err: {fe}")
                frame_count += 1
                if len(encodings) >= 20: break
            vid_cap.release()
            MIN_ENCODINGS = 5
        else:
            if os.path.exists(save_path): os.remove(save_path)
            return jsonify({'error': 'Unsupported file type.'}), 400

        if len(encodings) < MIN_ENCODINGS:
            if os.path.exists(save_path): os.remove(save_path)
            return jsonify({'error': f'Not enough clear faces. Found {len(encodings)}/{MIN_ENCODINGS}. Try better lighting.'}), 400

        student.face_encodings = encodings
        student.enrollment_data_path = web_path
        student.is_verified = True
        session.pop('update_verified', None)
        db.session.commit()
        return jsonify({'message': f'Face enrolled! {len(encodings)} samples saved.', 'reload': True})

    except Exception as e:
        db.session.rollback()
        traceback.print_exc()
        return jsonify({'error': f'Face processing error: {e}. Try again.'}), 500


@student_bp.route("/enroll-in-subject", methods=['POST'])
@login_required
@require_student
def enroll_in_subject():
    guard = _verified_student_or_redirect('Complete face enrollment before enrolling in subjects.')
    if guard:
        return guard
    subject_id = request.form.get('subject_id')
    if not subject_id:
        flash('Subject ID required.', 'danger'); return redirect(url_for('student.student_portal'))
    subject = db.session.get(Subject, subject_id)
    if not subject or subject.archived:
        flash('Subject not available.', 'danger'); return redirect(url_for('student.student_portal'))
    try:
        current_user.subjects.append(subject)
        db.session.commit()
        flash(f"Successfully enrolled in {subject.name}!", 'success')
    except Exception as e:
        db.session.rollback()
        flash("Already enrolled or error.", 'warning')
    return redirect(url_for('student.student_portal'))


@student_bp.route("/unenroll-from-subject", methods=['POST'])
@login_required
@require_student
def unenroll_from_subject():
    guard = _verified_student_or_redirect('Complete face enrollment before managing subjects.')
    if guard:
        return guard
    subject_id = request.form.get('subject_id')
    if not subject_id:
        flash('Subject ID required.', 'danger'); return redirect(url_for('student.student_portal'))
    subject = db.session.get(Subject, subject_id)
    if not subject:
        flash('Subject not found.', 'danger'); return redirect(url_for('student.student_portal'))
    try:
        current_user.subjects.remove(subject)
        db.session.commit()
        flash(f"Successfully unenrolled from {subject.name}.", 'success')
    except Exception as e:
        db.session.rollback()
        flash(f"Error: {e}", 'danger')
    return redirect(url_for('student.student_portal'))


@student_bp.route("/get-student-photos/<int:subject_id>/<string:date_str>")
@login_required
@require_student
def get_student_photos(subject_id, date_str):
    guard = _verified_student_or_json('Complete face enrollment before viewing attendance photos.')
    if guard:
        return guard
    roll = _canonical_current_student_roll()
    subject = db.session.get(Subject, subject_id)
    if not subject: return jsonify({'error': 'Subject not found'}), 404
    if subject not in current_user.subjects.all(): return jsonify({'error': 'Not enrolled'}), 403
    try:
        att_date = datetime.strptime(date_str, '%Y-%m-%d').date()
    except ValueError:
        return jsonify({'error': 'Invalid date format'}), 400
    photos = Photo.query.filter_by(subject_id=subject_id, attendance_date=att_date).order_by(Photo.id).all()
    photo_payload = []
    for photo in photos:
        approved_boxes = BoundingBox.query.filter_by(
            photo_id=photo.id,
            student_roll_number=roll,
            is_active=True,
            is_approved=True
        ).order_by(BoundingBox.id).all()
        pending_request_rows = BoundingBox.query.filter(
            BoundingBox.photo_id == photo.id,
            BoundingBox.student_roll_number == roll,
            BoundingBox.review_status == REVIEW_STATUS_PENDING,
            BoundingBox.identification_type.in_([IDENTIFICATION_TYPE_USER_ADDED, IDENTIFICATION_TYPE_USER_DELETED])
        ).order_by(BoundingBox.created_at, BoundingBox.id).all()

        pending_requests = []
        for request_group_id, request_boxes in group_boxes_by_request_group(pending_request_rows).items():
            add_boxes = []
            delete_boxes = []
            for box in request_boxes:
                payload = build_box_payload(box)
                if box.identification_type == IDENTIFICATION_TYPE_USER_ADDED:
                    add_boxes.append(payload)
                else:
                    delete_boxes.append({
                        **payload,
                        'source_box': build_box_payload(box.source_box) if box.source_box else None,
                        'bounding_box': box.source_box.bounding_box if box.source_box else box.bounding_box,
                    })

            pending_requests.append({
                'request_group_id': request_group_id,
                'created_at': request_boxes[0].created_at.isoformat() if request_boxes[0].created_at else None,
                'add_boxes': add_boxes,
                'delete_boxes': delete_boxes,
            })

        photo_payload.append({
            'id': photo.id,
            'captured_photo_name': photo.captured_photo_name,
            'raw_image_path': photo.raw_image_path,
            'image_width': photo.image_width,
            'image_height': photo.image_height,
            'approved_boxes': [build_box_payload(box) for box in approved_boxes],
            'pending_requests': pending_requests,
            'can_submit_changes': not pending_requests,
        })
    return jsonify(photo_payload)


@student_bp.route("/get-student-photo-dates/<int:subject_id>")
@login_required
@require_student
def get_student_photo_dates(subject_id):
    guard = _verified_student_or_json('Complete face enrollment before viewing attendance photos.')
    if guard:
        return guard
    subject = db.session.get(Subject, subject_id)
    if not subject: return jsonify({'error': 'Subject not found'}), 404
    if subject not in current_user.subjects.all(): return jsonify({'error': 'Not enrolled'}), 403
    dates = db.session.query(Photo.attendance_date)\
                      .filter(Photo.subject_id == subject_id).distinct()\
                      .order_by(desc(Photo.attendance_date)).all()
    return jsonify([d.attendance_date.strftime('%Y-%m-%d') for d in dates])


@student_bp.route("/get-student-attendance-data", methods=['GET'])
@login_required
@require_student
def get_student_attendance_data():
    guard = _verified_student_or_json('Complete face enrollment before viewing attendance data.')
    if guard:
        return guard
    subject_id = request.args.get('subject_id', type=int)
    if not subject_id: return jsonify({'error': 'Subject ID required.'}), 400
    roll = _canonical_current_student_roll()
    subject = db.session.get(Subject, subject_id)
    if not subject: return jsonify({'error': 'Subject not found.'}), 404
    if subject not in current_user.subjects.all(): return jsonify({'error': 'Not enrolled.'}), 403

    records = AttendanceRecord.query.filter_by(student_roll_number=roll, subject_id=subject_id)\
                              .order_by(desc(AttendanceRecord.attendance_date)).all()
    photo_dates = photo_backed_attendance_dates(subject_id)
    attendance_data = []; present_count = 0; absent_count = 0; medical_count = 0; other_count = 0
    for rec in records:
        source = infer_attendance_source(rec.source, rec.attendance_date, photo_dates)
        attendance_data.append({
            'date': rec.attendance_date.strftime('%Y-%m-%d'),
            'status': rec.status.value if isinstance(rec.status, AttendanceStatus) else rec.status,
            'source': source,
            'source_label': attendance_source_label(source),
            'is_manual': source == 'manual',
        })
        if rec.status == AttendanceStatus.PRESENT: present_count += 1
        elif rec.status == AttendanceStatus.ABSENT: absent_count += 1
        elif rec.status == AttendanceStatus.MEDICAL_LEAVE: medical_count += 1
        elif rec.status == AttendanceStatus.OTHER_LEAVE: other_count += 1

    total_marked = present_count + absent_count + medical_count + other_count
    # Student requested % presence (including ML/OL): (present + ML + OL) / Total
    percentage = round(((present_count + medical_count + other_count) / total_marked * 100), 1) if total_marked > 0 else 0.0

    latest = Photo.query.filter_by(subject_id=subject_id)\
                        .order_by(desc(Photo.attendance_date), desc(Photo.id)).first()
    photo_data = []; latest_date_str = None; status_today = 'N/A'
    if latest:
        latest_date_str = latest.attendance_date.strftime('%Y-%m-%d')
        status_rec = AttendanceRecord.query.filter_by(student_roll_number=roll,
                                                      attendance_date=latest.attendance_date,
                                                      subject_id=subject_id).first()
        if status_rec: status_today = status_rec.status
        photo_data = [
            p.annotated_image_path or p.raw_image_path
            for p in Photo.query.filter_by(subject_id=subject_id, attendance_date=latest.attendance_date).order_by(Photo.id).all()
        ]

    return jsonify({'attendance_history': attendance_data, 'present_count': present_count,
                    'absent_count': absent_count, 'medical_leave_count': medical_count,
                    'other_leave_count': other_count, 'total_marked': total_marked, 'percentage': percentage,
                    'annotated_photos': photo_data, 'most_recent_photo_date': latest_date_str,
                    'student_status_today': status_today.value if isinstance(status_today, AttendanceStatus) else status_today})


@student_bp.route("/student-bounding-box/submit", methods=['POST'])
@login_required
@require_student
def student_submit_bounding_box_request():
    guard = _verified_student_or_json('Complete face enrollment before requesting attendance corrections.')
    if guard:
        return guard

    photo_id = request.form.get('photo_id', type=int)
    if not photo_id:
        return jsonify({'error': 'Photo ID required.'}), 400

    photo = db.session.get(Photo, photo_id)
    if not photo:
        return jsonify({'error': 'Photo not found.'}), 404
    if photo.subject not in current_user.subjects.all():
        return jsonify({'error': 'Not enrolled in this subject.'}), 403

    try:
        remove_box_ids = _parse_json_list(request.form.get('remove_box_ids'), 'remove_box_ids')
        add_boxes = _parse_json_list(request.form.get('add_boxes'), 'add_boxes')
        submission = _submit_student_box_changes(photo, _canonical_current_student_roll(), remove_box_ids, add_boxes)
        db.session.commit()
        change_parts = []
        if submission['remove_count']:
            change_parts.append(f"{submission['remove_count']} deletion{'s' if submission['remove_count'] != 1 else ''}")
        if submission['add_count']:
            change_parts.append(f"{submission['add_count']} addition{'s' if submission['add_count'] != 1 else ''}")
        return jsonify({
            'message': f"Submitted {' and '.join(change_parts)} for staff review.",
            'request_group_id': submission['request_group_id'],
        })
    except ValueError as err:
        db.session.rollback()
        return jsonify({'error': str(err)}), 400
    except Exception as err:
        db.session.rollback()
        return jsonify({'error': f'Could not submit request: {err}'}), 500


@student_bp.route("/student-bounding-box/add", methods=['POST'])
@login_required
@require_student
def student_add_bounding_box():
    guard = _verified_student_or_json('Complete face enrollment before requesting attendance corrections.')
    if guard:
        return guard

    photo_id = request.form.get('photo_id', type=int)
    if not photo_id:
        return jsonify({'error': 'Photo ID required.'}), 400

    photo = db.session.get(Photo, photo_id)
    if not photo:
        return jsonify({'error': 'Photo not found.'}), 404
    if photo.subject not in current_user.subjects.all():
        return jsonify({'error': 'Not enrolled in this subject.'}), 403

    bbox = {
        'top': request.form.get('top'),
        'right': request.form.get('right'),
        'bottom': request.form.get('bottom'),
        'left': request.form.get('left'),
    }

    try:
        submission = _submit_student_box_changes(photo, _canonical_current_student_roll(), [], [bbox])
        db.session.commit()
        return jsonify({
            'message': 'Bounding box submitted for staff review.',
            'request_group_id': submission['request_group_id'],
        })
    except ValueError as err:
        db.session.rollback()
        return jsonify({'error': str(err)}), 400
    except Exception as err:
        db.session.rollback()
        return jsonify({'error': f'Could not submit request: {err}'}), 500


@student_bp.route("/student-bounding-box/delete", methods=['POST'])
@login_required
@require_student
def student_delete_bounding_box():
    guard = _verified_student_or_json('Complete face enrollment before requesting attendance corrections.')
    if guard:
        return guard

    source_box_id = request.form.get('box_id', type=int)
    if not source_box_id:
        return jsonify({'error': 'Bounding box ID required.'}), 400

    source_box = db.session.get(BoundingBox, source_box_id)
    student_roll = _canonical_current_student_roll()
    if not source_box or not source_box.photo:
        return jsonify({'error': 'Bounding box not found.'}), 404
    if _normalize_roll_for_compare(source_box.student_roll_number) != _normalize_roll_for_compare(student_roll):
        return jsonify({'error': 'You can only request deletion for your own bounding boxes.'}), 403
    if not source_box.is_active or not source_box.is_approved:
        return jsonify({'error': 'Only active approved boxes can be removed.'}), 409
    if source_box.photo.subject not in current_user.subjects.all():
        return jsonify({'error': 'Not enrolled in this subject.'}), 403

    try:
        submission = _submit_student_box_changes(source_box.photo, student_roll, [source_box.id], [])
        db.session.commit()
        return jsonify({
            'message': 'Delete request submitted for staff review.',
            'request_group_id': submission['request_group_id'],
        })
    except ValueError as err:
        db.session.rollback()
        return jsonify({'error': str(err)}), 400
    except Exception as err:
        db.session.rollback()
        return jsonify({'error': f'Could not submit request: {err}'}), 500
