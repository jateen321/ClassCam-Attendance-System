"""
routes/attendance.py — Attendance Blueprint
============================================
Covers: process attendance, view photos, attendance report,
        manual status update, download Excel report.

Blueprint name: 'attendance'
url_for prefix: url_for('attendance.process_attendance'), etc.
"""

import csv
import io
import json
import logging
import os
import traceback
import uuid
from datetime import datetime, date, timezone
from collections import defaultdict
from flask import Blueprint, request, redirect, url_for, flash, render_template, jsonify, Response
from flask_login import login_required, current_user
from sqlalchemy import desc
import numpy as np
import openpyxl
from app.extensions import db
from app.models import Teacher, Student, Subject, SubjectStaff, AttendanceRecord, BoundingBox, Photo, AttendanceStatus
from app.utils.attendance_review import (
    ATTENDANCE_SOURCE_MANUAL,
    ATTENDANCE_SOURCE_PHOTO,
    IDENTIFICATION_TYPE_ML,
    IDENTIFICATION_TYPE_USER_ADDED,
    IDENTIFICATION_TYPE_USER_DELETED,
    REVIEW_STATUS_APPROVED,
    REVIEW_STATUS_PENDING,
    REVIEW_STATUS_REJECTED,
    attendance_source_label,
    build_box_payload,
    build_review_event_payload,
    build_request_proposal,
    format_datetime_ist,
    group_boxes_by_request_group,
    infer_attendance_source,
    normalize_session_type,
    photo_backed_attendance_dates,
    recalculate_attendance_for_subject_date,
    replace_attendance_records_for_subject_date,
    registered_asset_kind,
    render_photo_annotation,
    sanitize_bounding_box,
    replace_ml_boxes_for_photo,
    static_web_path,
)
from app.utils.face import process_and_annotate_faces, face_executor
from app.utils.email import send_email
import cv2

logger = logging.getLogger(__name__)
attendance_bp = Blueprint('attendance', __name__)


def _annotation_dir():
    from flask import current_app
    d = os.path.join(current_app.static_folder, 'annotated_uploads')
    os.makedirs(d, exist_ok=True)
    return d


def _raw_dir():
    from flask import current_app
    d = os.path.join(current_app.static_folder, 'attendance_raw_uploads')
    os.makedirs(d, exist_ok=True)
    return d


def _teacher_has_subject_access(subject_id):
    if not (isinstance(current_user, Teacher) and current_user.is_approved):
        return False
    if current_user.role == 'Admin':
        return True
    return SubjectStaff.query.filter_by(
        teacher_id=current_user.id,
        subject_id=subject_id,
        is_approved_by_prof=True
    ).first() is not None


def _build_review_assets(student):
    assets = []
    seen = set()

    def add_asset(url, kind, label):
        if not url:
            return
        asset_kind = kind or 'image'
        key = (url, asset_kind)
        if key in seen:
            return
        seen.add(key)
        assets.append({
            'url': url,
            'kind': asset_kind,
            'label': label,
        })

    if student:
        add_asset(student.profile_photo_url, 'image', 'Profile photo')
        add_asset(student.enrollment_data_path, registered_asset_kind(student.enrollment_data_path), 'Registered asset')

    return assets


def _load_pending_request_group(request_group_id):
    if request_group_id.startswith('legacy-'):
        try:
            legacy_id = int(request_group_id.split('-', 1)[1])
        except (IndexError, ValueError):
            return []
        legacy_box = BoundingBox.query.filter(
            BoundingBox.id == legacy_id,
            BoundingBox.review_status == REVIEW_STATUS_PENDING,
            BoundingBox.identification_type.in_([IDENTIFICATION_TYPE_USER_ADDED, IDENTIFICATION_TYPE_USER_DELETED])
        ).first()
        return [legacy_box] if legacy_box else []

    return BoundingBox.query.filter(
        BoundingBox.request_group_id == request_group_id,
        BoundingBox.review_status == REVIEW_STATUS_PENDING,
        BoundingBox.identification_type.in_([IDENTIFICATION_TYPE_USER_ADDED, IDENTIFICATION_TYPE_USER_DELETED])
    ).order_by(BoundingBox.created_at, BoundingBox.id).all()


def _pending_group_is_consistent(request_boxes):
    if not request_boxes:
        return False
    photo_id = request_boxes[0].photo_id
    roll_number = request_boxes[0].student_roll_number
    return all(box.photo_id == photo_id and box.student_roll_number == roll_number for box in request_boxes)


def _normalize_roll_number(value):
    return (value or '').strip().upper()


def _build_subject_student_lookup(subject):
    students = subject.students.order_by(Student.roll_number).all()
    exact = {}
    normalized = {}
    ambiguous = set()

    for student in students:
        exact_key = (student.roll_number or '').strip()
        if exact_key:
            exact[exact_key] = student

        normalized_key = _normalize_roll_number(student.roll_number)
        if not normalized_key:
            continue

        existing = normalized.get(normalized_key)
        if existing and existing.roll_number != student.roll_number:
            ambiguous.add(normalized_key)
        else:
            normalized[normalized_key] = student

    return students, exact, normalized, ambiguous


def _resolve_subject_student(subject, roll_number, student_lookup=None):
    lookup = student_lookup or _build_subject_student_lookup(subject)
    _, exact, normalized, ambiguous = lookup
    raw_roll = (roll_number or '').strip()
    if not raw_roll:
        return None, 'Roll number is required.'

    if raw_roll in exact:
        return exact[raw_roll], None

    normalized_roll = _normalize_roll_number(raw_roll)
    if normalized_roll in ambiguous:
        return None, (
            f'Roll number "{raw_roll}" matches multiple enrolled students. '
            'Use the exact roll number shown in the platform.'
        )

    student = normalized.get(normalized_roll)
    if not student:
        return None, f'Student {raw_roll} is not enrolled in this subject.'
    return student, None


def _parse_manual_status(value):
    normalized = (value or '').strip().lower()
    if normalized in {'present', 'p', 'yes', 'y', '1', 'true'}:
        return 'present'
    if normalized in {'absent', 'a', 'no', 'n', '0', 'false'}:
        return 'absent'
    if normalized in {'medical leave', 'ml', 'medical'}:
        return 'medical_leave'
    if normalized in {'other leave', 'ol', 'other'}:
        return 'other_leave'
    return None


def _load_manual_attendance_status_map(subject, csv_file):
    content = csv_file.read()
    if not content:
        raise ValueError('The CSV file is empty.')

    decoded_content = content.decode('utf-8-sig', errors='replace')
    reader = csv.DictReader(io.StringIO(decoded_content))
    if not reader.fieldnames:
        raise ValueError('CSV headers are required. Include at least a roll number column.')

    header_map = {}
    for header in reader.fieldnames:
        cleaned = (header or '').strip().lower()
        if cleaned:
            header_map[cleaned] = header

    roll_field = next(
        (header_map[key] for key in ('student_roll_number', 'roll_number', 'roll') if key in header_map),
        None
    )
    status_field = next(
        (header_map[key] for key in ('status', 'attendance_status', 'attendance') if key in header_map),
        None
    )
    if not roll_field:
        raise ValueError('CSV must include a roll number column such as student_roll_number or roll_number.')

    enrolled_students, exact, normalized, ambiguous = _build_subject_student_lookup(subject)
    if not enrolled_students:
        raise ValueError('No students are enrolled in this subject.')

    student_lookup = (enrolled_students, exact, normalized, ambiguous)
    attendance_status_by_roll = {}
    processed_rows = 0

    for line_number, row in enumerate(reader, start=2):
        raw_roll = (row.get(roll_field) or '').strip()
        raw_status = (row.get(status_field) or '').strip() if status_field else ''
        if not raw_roll and not raw_status:
            continue
        if not raw_roll:
            raise ValueError(f'CSV row {line_number}: roll number is required.')

        student, error = _resolve_subject_student(subject, raw_roll, student_lookup)
        if error:
            raise ValueError(f'CSV row {line_number}: {error}')

        status = _parse_manual_status(raw_status) if status_field else 'present'
        if status_field and status is None:
            raise ValueError(
                f'CSV row {line_number}: status must be present/absent (or y/n, yes/no, 1/0).'
            )

        existing_status = attendance_status_by_roll.get(student.roll_number)
        if existing_status and existing_status != status:
            raise ValueError(f'CSV row {line_number}: conflicting statuses found for {student.roll_number}.')

        attendance_status_by_roll[student.roll_number] = status
        processed_rows += 1

    if processed_rows == 0:
        raise ValueError('The CSV file does not contain any attendance rows.')

    for student in enrolled_students:
        attendance_status_by_roll.setdefault(student.roll_number, 'absent')

    return attendance_status_by_roll, processed_rows


def _build_face_reference(subject, specific_roll_number=None):
    enrolled_students = subject.students.filter(
        Student.is_verified == True,
        Student.face_encodings.isnot(None)
    ).order_by(Student.roll_number).all()

    if specific_roll_number:
        enrolled_students = [student for student in enrolled_students if student.roll_number == specific_roll_number]

    known_encs = []
    known_data = []
    for student in enrolled_students:
        for encoding in student.face_encodings or []:
            try:
                known_encs.append(np.array(encoding))
                known_data.append((student.roll_number, student.name))
            except Exception as encoding_error:
                logger.warning("Encoding error for %s: %s", student.roll_number, encoding_error)

    return enrolled_students, known_encs, known_data


@attendance_bp.route("/process-attendance", methods=['POST'])
@login_required
def process_attendance():
    if not isinstance(current_user, Teacher):
        return jsonify({'error': 'Unauthorized.'}), 401
    try:
        subject_id = request.form.get('subject_id')
        att_date_str = request.form.get('attendance_date')
        images = request.files.getlist('photos')
        csv_file = request.files.get('attendance_csv')
        if not all([subject_id, att_date_str]):
            return jsonify({'error': 'Subject and Date required.'}), 400
        has_photos = bool(images and any(img.filename for img in images))
        has_csv = bool(csv_file and csv_file.filename)
        if not has_photos and not has_csv:
            return jsonify({'error': 'Upload at least one photo or one attendance CSV.'}), 400
        if has_photos and has_csv:
            return jsonify({'error': 'Choose either photos or a CSV file for one submission.'}), 400
        try:
            att_date = datetime.strptime(att_date_str, '%Y-%m-%d').date()
        except ValueError:
            return jsonify({'error': 'Invalid date format.'}), 400

        if not _teacher_has_subject_access(subject_id):
            return jsonify({'error': 'Unauthorized: Not approved for this subject.'}), 403

        subject = db.session.get(Subject, subject_id)
        if not subject: return jsonify({'error': 'Subject not found.'}), 404
        if subject.archived: return jsonify({'error': 'Subject is archived.'}), 400
        session_type = normalize_session_type(request.form.get('session_type'), subject.default_session_type)

        if has_csv:
            try:
                attendance_status_by_roll, processed_rows = _load_manual_attendance_status_map(subject, csv_file)
                attendance_stats = replace_attendance_records_for_subject_date(
                    subject.id,
                    att_date,
                    attendance_status_by_roll,
                    source=ATTENDANCE_SOURCE_MANUAL
                )
                db.session.commit()
            except ValueError as validation_error:
                db.session.rollback()
                return jsonify({'error': str(validation_error)}), 400
            except Exception as db_error:
                db.session.rollback()
                logger.exception("Manual CSV attendance save failed: %s", db_error)
                return jsonify({'error': f'DB save error: {db_error}'}), 500

            email_ok = True
            if request.form.get('send_email_notifications'):
                try:
                    enrolled_students = subject.students.order_by(Student.roll_number).all()
                    email_map = {student.roll_number: student.email for student in enrolled_students}
                    for roll_number in attendance_stats['present_rolls']:
                        if email_map.get(roll_number):
                            email_ok = send_email(
                                email_map[roll_number],
                                f"Attendance for {subject.name} ({att_date})",
                                f"Hi {roll_number},\n\nYou have been marked PRESENT for {subject.name} on {att_date}."
                            ) and email_ok
                    for roll_number in attendance_stats['absent_rolls']:
                        if email_map.get(roll_number):
                            email_ok = send_email(
                                email_map[roll_number],
                                f"Attendance for {subject.name} ({att_date})",
                                f"Hi {roll_number},\n\nYou have been marked ABSENT for {subject.name} on {att_date}."
                            ) and email_ok
                except Exception:
                    email_ok = False

            return jsonify({
                'message': (
                    f'Manual attendance for {subject.name} saved from CSV!'
                    f'{" " if not email_ok else ""}'
                    f'{"(Email send issues.)" if not email_ok else ""}'
                ).strip(),
                'present_count': attendance_stats['present_count'],
                'absent_count': attendance_stats['absent_count'],
                'medical_leave_count': attendance_stats['medical_leave_count'],
                'other_leave_count': attendance_stats['other_leave_count'],
                'csv_rows_processed': processed_rows,
                'attendance_source': ATTENDANCE_SOURCE_MANUAL,
                'attendance_source_label': attendance_source_label(ATTENDANCE_SOURCE_MANUAL),
                'annotated_images': [],
                'faces_detected': 0,
                'identified_in_photos': 0
            })

        enrolled_students, known_encs, known_data = _build_face_reference(subject)
        if not known_encs:
            return jsonify({'error': 'No verified students enrolled in this subject.'}), 400

        annotated_paths = []
        processed_at_least_one = False
        total_faces = 0
        identified_this_submission = set()

        for img_file in images:
            if img_file.filename == '':
                continue
            img_bytes = img_file.read()
            ext = os.path.splitext(img_file.filename)[1] or '.jpg'
            att_date_fmt = att_date.strftime('%Y-%m-%d')
            nparr = np.frombuffer(img_bytes, np.uint8)
            raw_img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
            if raw_img is None:
                continue

            raw_fname = f"{subject.code}_{att_date_fmt}_{uuid.uuid4()}{ext}"
            raw_fpath = os.path.join(_raw_dir(), raw_fname)
            try:
                with open(raw_fpath, 'wb') as f:
                    f.write(img_bytes)
            except Exception as raw_err:
                logger.warning(f"Raw save error {raw_fname}: {raw_err}")
                continue

            raw_web_path = static_web_path('attendance_raw_uploads', raw_fname)
            photo = Photo(
                subject_id=subject.id,
                academic_year=subject.academic_year,
                semester=subject.semester,
                attendance_date=att_date,
                session_type=session_type,
                captured_photo_name=raw_fname,
                raw_image_path=raw_web_path,
                image_width=raw_img.shape[1],
                image_height=raw_img.shape[0],
                teacher_id=current_user.id
            )
            db.session.add(photo)
            db.session.flush()

            # Submit heavy CPU task to the background pool and wait for it
            future = face_executor.submit(process_and_annotate_faces, img_bytes, known_encs, known_data)
            detections, _, faces_count = future.result()
            
            total_faces += faces_count
            identified_this_submission.update(replace_ml_boxes_for_photo(photo, detections))
            if photo.annotated_image_path:
                processed_at_least_one = True
                annotated_paths.append(photo.annotated_image_path)

        if not processed_at_least_one and not identified_this_submission:
            return jsonify({'error': 'Could not process submitted photos.'}), 400

        try:
            attendance_stats = recalculate_attendance_for_subject_date(subject.id, att_date)
            db.session.commit()
        except Exception as db_err:
            db.session.rollback()
            traceback.print_exc()
            return jsonify({'error': f'DB save error: {db_err}'}), 500

        email_ok = True
        if request.form.get('send_email_notifications'):
            try:
                email_map = {s.roll_number: s.email for s in enrolled_students}
                for r in attendance_stats['present_rolls']:
                    if email_map.get(r):
                        email_ok = send_email(email_map[r], f"Attendance for {subject.name} ({att_date})",
                                              f"Hi {r},\n\nYou have been marked PRESENT for {subject.name} on {att_date}.") and email_ok
                for r in attendance_stats['absent_rolls']:
                    if email_map.get(r):
                        email_ok = send_email(email_map[r], f"Attendance for {subject.name} ({att_date})",
                                              f"Hi {r},\n\nYou have been marked ABSENT for {subject.name} on {att_date}.") and email_ok
            except Exception:
                email_ok = False

        return jsonify({
            'message': f'Attendance for {subject.name} saved!{"" if email_ok else " (Email send issues.)"}',
            'annotated_images': annotated_paths,
            'faces_detected': total_faces,
            'identified_in_photos': len(identified_this_submission),
            'present_count': attendance_stats['present_count'],
            'absent_count': attendance_stats['absent_count'],
            'medical_leave_count': attendance_stats['medical_leave_count'],
            'other_leave_count': attendance_stats['other_leave_count'],
        })
    except Exception as e:
        db.session.rollback(); traceback.print_exc()
        return jsonify({'error': f'Internal error: {e}'}), 500


@attendance_bp.route("/get-photo-dates/<int:subject_id>")
@login_required
def get_photo_dates(subject_id):
    if not isinstance(current_user, Teacher):
        return jsonify({'error': 'Unauthorized'}), 401
    if not _teacher_has_subject_access(subject_id):
        return jsonify({'error': 'Unauthorized for this subject'}), 403
    dates = db.session.query(Photo.attendance_date)\
                      .filter(Photo.subject_id == subject_id).distinct()\
                      .order_by(desc(Photo.attendance_date)).all()
    return jsonify([d.attendance_date.strftime('%Y-%m-%d') for d in dates])


@attendance_bp.route("/get-photos/<int:subject_id>/<string:date_str>")
@login_required
def get_photos(subject_id, date_str):
    if not isinstance(current_user, Teacher):
        return jsonify({'error': 'Unauthorized'}), 401
    if not _teacher_has_subject_access(subject_id):
        return jsonify({'error': 'Unauthorized for this subject'}), 403
    try:
        att_date = datetime.strptime(date_str, '%Y-%m-%d').date()
    except ValueError:
        return jsonify({'error': 'Invalid date format'}), 400
    photos = Photo.query.filter_by(subject_id=subject_id, attendance_date=att_date)\
                        .order_by(Photo.id).all()
    photo_payload = []
    for photo in photos:
        approved_request_rows = BoundingBox.query.filter(
            BoundingBox.photo_id == photo.id,
            BoundingBox.review_status == REVIEW_STATUS_APPROVED,
            BoundingBox.identification_type.in_([IDENTIFICATION_TYPE_USER_ADDED, IDENTIFICATION_TYPE_USER_DELETED]),
            BoundingBox.created_by_student_roll_number.isnot(None)
        ).order_by(BoundingBox.created_at, BoundingBox.id).all()
        review_events = []
        for _, request_boxes in group_boxes_by_request_group(approved_request_rows).items():
            event_payload = build_review_event_payload(request_boxes)
            if event_payload:
                review_events.append(event_payload)

        photo_payload.append({
            'id': photo.id,
            'captured_photo_name': photo.captured_photo_name,
            'image_path': photo.annotated_image_path or photo.raw_image_path,
            'review_events': review_events,
        })
    return jsonify(photo_payload)


@attendance_bp.route("/bounding-box-review-queue", methods=['GET'])
@login_required
def bounding_box_review_queue():
    if not (isinstance(current_user, Teacher) and current_user.is_approved):
        return jsonify({'error': 'Unauthorized'}), 401

    query = BoundingBox.query.join(Photo).filter(
        BoundingBox.identification_type.in_([IDENTIFICATION_TYPE_USER_ADDED, IDENTIFICATION_TYPE_USER_DELETED]),
        BoundingBox.review_status == REVIEW_STATUS_PENDING
    )
    if current_user.role != 'Admin':
        query = query.join(SubjectStaff, SubjectStaff.subject_id == Photo.subject_id).filter(
            SubjectStaff.teacher_id == current_user.id,
            SubjectStaff.is_approved_by_prof == True
        )

    queue_items = []
    pending_rows = query.order_by(desc(Photo.attendance_date), desc(BoundingBox.created_at), desc(BoundingBox.id)).all()
    for request_group_id, request_boxes in group_boxes_by_request_group(pending_rows).items():
        request_box = request_boxes[0]
        photo = request_box.photo
        student = request_box.box_student or request_box.requested_by_student
        approved_boxes = BoundingBox.query.filter_by(
            photo_id=photo.id,
            is_active=True,
            is_approved=True
        ).order_by(BoundingBox.id).all()
        proposal = build_request_proposal(approved_boxes, request_boxes)
        delete_boxes_payload = []
        for delete_box in proposal['delete_boxes']:
            delete_boxes_payload.append({
                **build_box_payload(delete_box),
                'source_box': build_box_payload(delete_box.source_box) if delete_box.source_box else None,
                'bounding_box': delete_box.source_box.bounding_box if delete_box.source_box else delete_box.bounding_box,
            })

        queue_items.append({
            'request_group_id': request_group_id,
            'subject_id': photo.subject_id,
            'subject_name': photo.subject.name,
            'subject_code': photo.subject.code,
            'academic_year': photo.academic_year,
            'semester': photo.semester,
            'attendance_date': photo.attendance_date.strftime('%Y-%m-%d'),
            'session_type': photo.session_type,
            'student_roll_number': request_box.student_roll_number,
            'student_name': student.name if student else request_box.student_roll_number,
            'student_profile_photo_url': student.profile_photo_url if student else None,
            'registered_asset_path': student.enrollment_data_path if student else None,
            'registered_asset_kind': registered_asset_kind(student.enrollment_data_path if student else None),
            'review_assets': _build_review_assets(student),
            'submitted_at': request_box.created_at.isoformat() if request_box.created_at else None,
            'submitted_at_ist': format_datetime_ist(request_box.created_at),
            'add_count': len(proposal['add_boxes']),
            'delete_count': len(proposal['delete_boxes']),
            'photo': {
                'id': photo.id,
                'captured_photo_name': photo.captured_photo_name,
                'raw_image_path': photo.raw_image_path,
                'image_width': photo.image_width,
                'image_height': photo.image_height,
            },
            'current_boxes': [build_box_payload(box) for box in approved_boxes],
            'add_boxes': [build_box_payload(box) for box in proposal['add_boxes']],
            'delete_boxes': delete_boxes_payload,
            'proposed_boxes': [build_box_payload(box) for box in proposal['proposed_boxes']],
        })

    return jsonify(queue_items)


@attendance_bp.route("/review-bounding-box/<string:request_group_id>", methods=['POST'])
@login_required
def review_bounding_box(request_group_id):
    if not (isinstance(current_user, Teacher) and current_user.is_approved):
        return jsonify({'error': 'Unauthorized'}), 401

    request_boxes = _load_pending_request_group(request_group_id)
    if not request_boxes:
        return jsonify({'error': 'Review item not found.'}), 404
    if not _pending_group_is_consistent(request_boxes):
        return jsonify({'error': 'The pending review group is inconsistent.'}), 409

    request_box = request_boxes[0]
    if not _teacher_has_subject_access(request_box.photo.subject_id):
        return jsonify({'error': 'Unauthorized for this subject.'}), 403

    decision = (request.form.get('decision') or '').strip().lower()
    if decision not in {'approve', 'reject'}:
        return jsonify({'error': 'Decision must be approve or reject.'}), 400

    now = datetime.now(timezone.utc)

    try:
        if decision == 'approve':
            add_request_boxes = [
                box for box in request_boxes
                if box.identification_type == IDENTIFICATION_TYPE_USER_ADDED
            ]
            delete_request_boxes = [
                box for box in request_boxes
                if box.identification_type == IDENTIFICATION_TYPE_USER_DELETED
            ]

            raw_add_boxes = request.form.get('add_boxes')
            modified_add_boxes = None
            if raw_add_boxes not in (None, ''):
                try:
                    modified_add_boxes = json.loads(raw_add_boxes)
                except json.JSONDecodeError:
                    return jsonify({'error': 'Invalid add_boxes payload.'}), 400
                if not isinstance(modified_add_boxes, list):
                    return jsonify({'error': 'add_boxes must be a list.'}), 400
                if len(modified_add_boxes) != len(add_request_boxes):
                    return jsonify({'error': 'Modified add box count does not match the request.'}), 400

            if modified_add_boxes is not None:
                for pending_box, raw_bbox in zip(add_request_boxes, modified_add_boxes):
                    bbox = sanitize_bounding_box(raw_bbox, request_box.photo.image_width, request_box.photo.image_height)
                    if not bbox:
                        return jsonify({'error': 'One or more modified bounding boxes are invalid.'}), 400
                    pending_box.bounding_box = bbox

            for delete_request in delete_request_boxes:
                source_box = delete_request.source_box
                if not source_box or not source_box.is_active or not source_box.is_approved:
                    return jsonify({'error': 'One or more source boxes are no longer active.'}), 409

            for pending_box in add_request_boxes:
                pending_box.is_approved = True
                pending_box.review_status = REVIEW_STATUS_APPROVED
                pending_box.is_active = True
                pending_box.reviewed_by_teacher_id = current_user.id
                pending_box.reviewed_at = now

            for delete_request in delete_request_boxes:
                delete_request.is_approved = True
                delete_request.review_status = REVIEW_STATUS_APPROVED
                delete_request.is_active = False
                delete_request.reviewed_by_teacher_id = current_user.id
                delete_request.reviewed_at = now

                source_box = delete_request.source_box
                source_box.identification_type = IDENTIFICATION_TYPE_USER_DELETED
                source_box.is_active = False
                source_box.reviewed_by_teacher_id = current_user.id
                source_box.reviewed_at = now

            render_photo_annotation(request_box.photo)
            recalculate_attendance_for_subject_date(request_box.photo.subject_id, request_box.photo.attendance_date)
            db.session.commit()
            return jsonify({'message': 'Bounding box request approved.'})

        for pending_box in request_boxes:
            pending_box.review_status = REVIEW_STATUS_REJECTED
            pending_box.is_active = False
            pending_box.reviewed_by_teacher_id = current_user.id
            pending_box.reviewed_at = now
        db.session.commit()
        return jsonify({'message': 'Bounding box request rejected.'})
    except Exception as err:
        db.session.rollback()
        logger.exception("Bounding box review failed: %s", err)
        return jsonify({'error': f'Review failed: {err}'}), 500


@attendance_bp.route("/report")
@login_required
def report():
    subject_id = request.args.get('subject_id', type=int)
    if not subject_id:
        flash('No subject selected.', 'danger')
        return redirect(url_for('teacher.teacher_portal') if isinstance(current_user, Teacher) else url_for('student.student_portal'))

    subject = db.session.get(Subject, subject_id)
    if not subject:
        flash('Subject not found.', 'danger'); return redirect(url_for('main.index'))

    is_teacher_view = False
    if isinstance(current_user, Teacher) and current_user.is_approved:
        if current_user.role == 'Admin':
            is_teacher_view = True
        else:
            if SubjectStaff.query.filter_by(teacher_id=current_user.id, subject_id=subject_id, is_approved_by_prof=True).first():
                is_teacher_view = True
    elif isinstance(current_user, Student):
        if subject not in current_user.subjects.all():
            flash('Unauthorized: You are not enrolled in this subject.', 'danger')
            return redirect(url_for('student.student_portal'))
    else:
        flash('Unauthorized.', 'danger'); return redirect(url_for('main.index'))

    try:
        students = subject.students.filter_by(is_verified=True).order_by(Student.roll_number).all()
        records = AttendanceRecord.query.filter_by(subject_id=subject_id).order_by(AttendanceRecord.attendance_date).all()
        unique_dates_str = [d.strftime('%Y-%m-%d') for d in sorted({rec.attendance_date for rec in records})]
        photo_dates = photo_backed_attendance_dates(subject_id)

        students_data = [{
            'roll_number': s.roll_number, 'name': s.name, 'email': s.email,
            'attendance': {d: '-' for d in unique_dates_str},
            'attendance_source': {d: None for d in unique_dates_str},
            'present_count': 0, 'absent_count': 0, 'medical_leave_count': 0, 'other_leave_count': 0,
            'total_marked': 0, 'percentage': 0.0
        } for s in students]
        student_map = {sd['roll_number']: sd for sd in students_data}
        daily_totals = {d: {'present': 0, 'absent': 0, 'medical_leave': 0, 'other_leave': 0} for d in unique_dates_str}

        for rec in records:
            if rec.student_roll_number in student_map:
                entry = student_map[rec.student_roll_number]
                ds = rec.attendance_date.strftime('%Y-%m-%d')
                # Use .value for templates/JSON
                status_str = rec.status.value if isinstance(rec.status, AttendanceStatus) else rec.status
                entry['attendance'][ds] = status_str
                entry['attendance_source'][ds] = infer_attendance_source(rec.source, rec.attendance_date, photo_dates)
                if rec.status == AttendanceStatus.PRESENT:
                    entry['present_count'] += 1; daily_totals[ds]['present'] += 1
                elif rec.status == AttendanceStatus.ABSENT:
                    entry['absent_count'] += 1; daily_totals[ds]['absent'] += 1
                elif rec.status == AttendanceStatus.MEDICAL_LEAVE:
                    entry['medical_leave_count'] += 1; daily_totals[ds]['medical_leave'] += 1
                elif rec.status == AttendanceStatus.OTHER_LEAVE:
                    entry['other_leave_count'] += 1; daily_totals[ds]['other_leave'] += 1

        for entry in students_data:
            entry['total_marked'] = entry['present_count'] + entry['absent_count'] + \
                                    entry['medical_leave_count'] + entry['other_leave_count']
            if entry['total_marked'] > 0:
                # User requested % absence: absent / (present + absent + ML + OL)
                entry['percentage'] = round((entry['absent_count'] / entry['total_marked']) * 100, 1)

        total_days = len(unique_dates_str)
        longest_name_len = max((len((entry['name'] or '').strip()) for entry in students_data), default=0)
        name_col_width = max(140, int(longest_name_len * 8.5 + 24)) if longest_name_len else 140

        return render_template('report.html', students_data=students_data, unique_dates=unique_dates_str,
                               is_teacher_view=is_teacher_view, subject=subject, daily_totals=daily_totals,
                               total_days=total_days, name_col_width=name_col_width)
    except Exception as e:
        traceback.print_exc(); flash(f"Report Error: {e}", 'danger')
        return redirect(url_for('teacher.teacher_portal') if isinstance(current_user, Teacher) else url_for('student.student_portal'))


@attendance_bp.route("/update-attendance-status", methods=['POST'])
@login_required
def update_attendance_status():
    if not (isinstance(current_user, Teacher) and current_user.is_approved):
        return jsonify({'error': 'Unauthorized'}), 401
    try:
        data = request.json
        raw_roll = data.get('roll_number')
        date_str = data.get('date'); status = data.get('status'); subject_id = data.get('subject_id')
        if not all([raw_roll, date_str, status, subject_id]):
            return jsonify({'error': 'Missing data.'}), 400
        subject = db.session.get(Subject, subject_id)
        if not subject: return jsonify({'error': 'Subject not found.'}), 404
        if subject.archived: return jsonify({'error': 'Subject is archived.'}), 400
        staff_link = SubjectStaff.query.filter_by(teacher_id=current_user.id, subject_id=subject_id, is_approved_by_prof=True).first()
        if not staff_link and current_user.role != 'Admin':
            return jsonify({'error': 'Unauthorized for this subject.'}), 403
        # Validate status against AttendanceStatus Enum values
        try:
            if status != '-':
                # This works because AttendanceStatus now inherits from str
                _ = AttendanceStatus(status)
        except ValueError:
            return jsonify({'error': 'Invalid status.'}), 400
        student, resolve_error = _resolve_subject_student(subject, raw_roll)
        if resolve_error:
            return jsonify({'error': resolve_error}), 404
        roll = student.roll_number
        try:
            att_date = date.fromisoformat(date_str)
        except ValueError:
            return jsonify({'error': 'Invalid date format.'}), 400
        record = AttendanceRecord.query.filter_by(student_roll_number=roll, attendance_date=att_date, subject_id=subject_id).first()
        if status == '-':
            if record:
                db.session.delete(record)
        else:
            status_enum = AttendanceStatus(status)
            if record:
                record.status = status_enum
                record.source = ATTENDANCE_SOURCE_MANUAL
            else:
                db.session.add(AttendanceRecord(
                    subject_id=subject_id,
                    student_roll_number=roll,
                    attendance_date=att_date,
                    status=status_enum,
                    source=ATTENDANCE_SOURCE_MANUAL
                ))
        db.session.commit()
        is_manual = status != '-'
        return jsonify({
            'message': 'Attendance updated!',
            'attendance_source': ATTENDANCE_SOURCE_MANUAL if is_manual else None,
            'attendance_source_label': attendance_source_label(ATTENDANCE_SOURCE_MANUAL) if is_manual else None,
        })
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': f'DB error: {e}'}), 500


@attendance_bp.route("/download-report")
@login_required
def download_report():
    if not (isinstance(current_user, Teacher) and current_user.is_approved):
        flash('Unauthorized.', 'danger'); return redirect(url_for('teacher.teacher_portal'))
    subject_id = request.args.get('subject_id', type=int)
    if not subject_id:
        flash('Subject ID required.', 'danger'); return redirect(url_for('teacher.teacher_portal'))
    subject = db.session.get(Subject, subject_id)
    if not subject:
        flash('Subject not found.', 'danger'); return redirect(url_for('teacher.teacher_portal'))
    staff_link = SubjectStaff.query.filter_by(teacher_id=current_user.id, subject_id=subject_id, is_approved_by_prof=True).first()
    if not staff_link and current_user.role != 'Admin':
        flash('Unauthorized for this subject.', 'danger'); return redirect(url_for('teacher.teacher_portal'))
    try:
        students = subject.students.filter_by(is_verified=True).order_by(Student.roll_number).all()
        records = AttendanceRecord.query.filter_by(subject_id=subject_id).order_by(AttendanceRecord.attendance_date).all()
        unique_dates_str = [d.strftime('%Y-%m-%d') for d in sorted({rec.attendance_date for rec in records})]
        attendance_map = defaultdict(lambda: {d: '-' for d in unique_dates_str})
        for r in records:
            ds = r.attendance_date.strftime('%Y-%m-%d')
            attendance_map[r.student_roll_number][ds] = r.status

        wb = openpyxl.Workbook(); ws = wb.active; ws.title = f"{subject.code} Report"
        ws.append(["Student_Rollno"] + [f"Class_{i+1}" for i in range(len(unique_dates_str))])
        ws.append([""] + unique_dates_str)
        for s in students:
            row = [s.roll_number] + [
                'P' if attendance_map[s.roll_number].get(d) == AttendanceStatus.PRESENT else
                'A' if attendance_map[s.roll_number].get(d) == AttendanceStatus.ABSENT else
                'ML' if attendance_map[s.roll_number].get(d) == AttendanceStatus.MEDICAL_LEAVE else
                'OL' if attendance_map[s.roll_number].get(d) == AttendanceStatus.OTHER_LEAVE else ''
                for d in unique_dates_str
            ]
            ws.append(row)

        stream = io.BytesIO(); wb.save(stream); stream.seek(0)
        fname = f"attendance_{subject.code}_{datetime.now().strftime('%Y%m%d')}.xlsx"
        return Response(stream,
                        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                        headers={'Content-Disposition': f'attachment;filename={fname}'})
    except Exception as e:
        traceback.print_exc(); flash(f"Excel Error: {e}", 'danger')
        return redirect(url_for('teacher.teacher_portal'))
