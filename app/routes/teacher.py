"""
routes/teacher.py — Teacher Blueprint
=======================================
Covers: teacher portal, admin management (approve/deny/remove staff/students),
        subject management (create, archive, unarchive, change professor),
        TA management (add, approve, remove).

Blueprint name: 'teacher'
url_for prefix: url_for('teacher.teacher_portal'), etc.
"""

import logging
import os
import uuid
from werkzeug.utils import secure_filename
from flask import Blueprint, render_template, request, flash, redirect, url_for, session, current_app, jsonify
from flask_login import login_required, current_user
from sqlalchemy import func
from app.extensions import db
from app.models import Teacher, Student, Subject, SubjectStaff
from app.utils.attendance_review import normalize_session_type
from app.utils.decorators import require_role
from app.utils.email import send_email, send_email_async
from app.utils.db_helpers import delete_teacher_with_dependencies

logger = logging.getLogger(__name__)
teacher_bp = Blueprint('teacher', __name__)


@teacher_bp.route("/teacher")
def teacher_portal():
    pending_teachers = []; approved_teachers = []; all_students = []
    subjects_managed = []; available_tas = []; pending_ta_requests = []; archived_subjects = []
    attendance_subjects = []
    available_professors = []

    if current_user.is_authenticated and isinstance(current_user, Teacher) and current_user.is_approved:
        if current_user.role == 'Admin':
            pending_teachers = Teacher.query.filter_by(is_approved=False).order_by(Teacher.id).all()
            approved_teachers = Teacher.query.filter(Teacher.is_approved == True, Teacher.id != current_user.id).order_by(Teacher.username).all()
            all_students = Student.query.order_by(Student.roll_number).all()
            attendance_subjects = Subject.query.filter_by(archived=False).order_by(Subject.name).all()
            subjects_managed = Subject.query.order_by(Subject.name).all()
            archived_subjects = [s for s in subjects_managed if s.archived]
            available_tas = Teacher.query.filter_by(role='TA', is_approved=True).all()
            available_professors = Teacher.query.filter_by(role='Professor', is_approved=True).all()
            pending_ta_requests = SubjectStaff.query.filter(
                SubjectStaff.role_in_subject == 'TA',
                SubjectStaff.is_approved_by_prof == False
            ).all()

        elif current_user.role == 'Professor':
            subjects_managed = Subject.query.join(SubjectStaff).filter(
                SubjectStaff.teacher_id == current_user.id,
                SubjectStaff.role_in_subject == 'Professor'
            ).order_by(Subject.name).all()
            attendance_subjects = [s for s in subjects_managed if not s.archived]
            archived_subjects = [s for s in subjects_managed if s.archived]
            available_tas = Teacher.query.filter_by(role='TA', is_approved=True).all()
            subject_ids = [s.id for s in subjects_managed]
            if subject_ids:
                pending_ta_requests = SubjectStaff.query.filter(
                    SubjectStaff.subject_id.in_(subject_ids),
                    SubjectStaff.is_approved_by_prof == False,
                    SubjectStaff.role_in_subject == 'TA'
                ).all()

        elif current_user.role == 'TA':
            attendance_subjects = Subject.query.join(SubjectStaff).filter(
                SubjectStaff.teacher_id == current_user.id,
                SubjectStaff.is_approved_by_prof == True,
                Subject.archived == False
            ).order_by(Subject.name).all()

    if subjects_managed:
        for subject in subjects_managed:
            prof_assign = next((sa for sa in subject.staff_assignments if sa.role_in_subject == 'Professor'), None)
            subject.professor_name = prof_assign.teacher.username if prof_assign else "Not Assigned"

    all_subjects_for_photo_display = []
    if current_user.is_authenticated and isinstance(current_user, Teacher) and current_user.is_approved:
        if current_user.role == 'Admin':
            all_subjects_for_photo_display = Subject.query.order_by(Subject.name).all()
        elif current_user.role == 'Professor':
            all_subjects_for_photo_display = Subject.query.join(SubjectStaff).filter(
                SubjectStaff.teacher_id == current_user.id,
                SubjectStaff.role_in_subject == 'Professor'
            ).order_by(Subject.name).all()
        elif current_user.role == 'TA':
            all_subjects_for_photo_display = Subject.query.join(SubjectStaff).filter(
                SubjectStaff.teacher_id == current_user.id,
                SubjectStaff.is_approved_by_prof == True
            ).order_by(Subject.name).all()

    return render_template(
        'teacher.html',
        pending_teachers=pending_teachers, approved_teachers=approved_teachers,
        all_students=all_students, subjects_managed=subjects_managed,
        available_tas=available_tas, available_professors=available_professors,
        pending_ta_requests=pending_ta_requests, attendance_subjects=attendance_subjects,
        archived_subjects=archived_subjects, all_subjects_for_photo_display=all_subjects_for_photo_display
    )


@teacher_bp.route("/approve-teacher/<int:teacher_id>", methods=['POST'])
@login_required
@require_role('Admin')
def approve_teacher(teacher_id):
    teacher = db.session.get(Teacher, teacher_id)
    if not teacher:
        flash('Not found.', 'danger')
    elif teacher.is_approved:
        flash(f'{teacher.username} already approved.', 'info')
    else:
        try:
            teacher.is_approved = True
            db.session.commit()
            flash(f'{teacher.username} approved!', 'success')
            send_email_async(teacher.email, "Account Approved", f"Hi {teacher.username},\nAccount approved.")
        except Exception as e:
            db.session.rollback()
            flash(f'Error: {e}', 'danger')
    return redirect(url_for('teacher.teacher_portal'))


@teacher_bp.route("/deny-teacher/<int:teacher_id>", methods=['POST'])
@login_required
@require_role('Admin')
def deny_teacher(teacher_id):
    teacher = db.session.get(Teacher, teacher_id)
    if not teacher:
        flash('Not found.', 'danger')
    elif teacher.is_approved:
        flash(f'Cannot deny approved teacher ({teacher.username}).', 'warning')
    else:
        name = teacher.username; email = teacher.email
        try:
            delete_teacher_with_dependencies(teacher)
            db.session.commit()
        except ValueError as guard_err:
            db.session.rollback()
            flash(str(guard_err), 'warning')
            return redirect(url_for('teacher.teacher_portal'))
        except Exception as e:
            db.session.rollback()
            flash(f'Error: {e}', 'danger')
            return redirect(url_for('teacher.teacher_portal'))
        flash(f'Registration for {name} denied.', 'success')
        if not send_email(email, "Registration Denied", f"Hi {name},\nRegistration denied."):
            logger.warning(f"Post-denial email failed for {email}")
    return redirect(url_for('teacher.teacher_portal'))


@teacher_bp.route("/remove-teacher/<int:teacher_id>", methods=['POST'])
@login_required
@require_role('Admin')
def remove_teacher(teacher_id):
    if current_user.id == teacher_id:
        flash('Cannot remove self.', 'danger')
        return redirect(url_for('teacher.teacher_portal'))
    teacher = db.session.get(Teacher, teacher_id)
    if not teacher:
        flash('Not found.', 'danger')
    else:
        name = teacher.username; email = teacher.email
        try:
            delete_teacher_with_dependencies(teacher)
            db.session.commit()
        except ValueError as guard_err:
            db.session.rollback()
            flash(str(guard_err), 'warning')
            return redirect(url_for('teacher.teacher_portal'))
        except Exception as e:
            db.session.rollback()
            flash(f'Error: {e}', 'danger')
            return redirect(url_for('teacher.teacher_portal'))
        flash(f'{name} removed.', 'success')
        if not send_email(email, "Account Removed", f"Hi {name},\nAccount removed."):
            logger.warning(f"Post-removal email failed for {email}")
    return redirect(url_for('teacher.teacher_portal'))


@teacher_bp.route("/remove-student/<string:roll_number>", methods=['POST'])
@login_required
@require_role('Admin')
def remove_student(roll_number):
    normalized_roll = (roll_number or "").strip().upper()
    student = db.session.get(Student, normalized_roll)
    if not student and normalized_roll:
        student = Student.query.filter(func.upper(Student.roll_number) == normalized_roll).first()
    if not student and normalized_roll:
        student = Student.query.filter(func.upper(func.trim(Student.roll_number)) == normalized_roll).first()
    if not student:
        flash(f'Student {normalized_roll or roll_number} not found.', 'danger')
    else:
        try:
            name = student.name; email = student.email
            db.session.delete(student)
            db.session.commit()
            flash(f'Student {name} ({student.roll_number}) removed.', 'success')
            send_email_async(email, "Account Removed", f"Hi {name},\nStudent account removed.")
        except Exception as e:
            db.session.rollback()
            flash(f'Error: {e}', 'danger')
    return redirect(url_for('teacher.teacher_portal'))


@teacher_bp.route("/admin-remove-person", methods=['POST'])
@login_required
@require_role('Admin')
def admin_remove_person():
    person_type = (request.form.get('person_type') or '').strip().lower()
    identifier = (request.form.get('identifier') or '').strip()

    if not person_type or not identifier:
        flash('Person type and identifier are required.', 'danger')
        return redirect(url_for('teacher.teacher_portal'))

    if person_type == 'student':
        normalized_roll = identifier.upper()
        student = db.session.get(Student, normalized_roll)
        if not student:
            student = Student.query.filter(func.lower(Student.email) == identifier.lower()).first()
        if not student and normalized_roll:
            student = Student.query.filter(func.upper(Student.roll_number) == normalized_roll).first()
        if not student and normalized_roll:
            student = Student.query.filter(func.upper(func.trim(Student.roll_number)) == normalized_roll).first()
        if not student:
            flash(f'Student not found for "{identifier}".', 'danger')
            return redirect(url_for('teacher.teacher_portal'))
        try:
            name = student.name; email = student.email; roll = student.roll_number
            db.session.delete(student)
            db.session.commit()
            flash(f'Student {name} ({roll}) removed.', 'success')
            send_email_async(email, "Account Removed", f"Hi {name},\nStudent account removed.")
        except Exception as e:
            db.session.rollback()
            flash(f'Error: {e}', 'danger')
        return redirect(url_for('teacher.teacher_portal'))

    if person_type == 'teacher':
        teacher = None
        if identifier.isdigit():
            teacher = db.session.get(Teacher, int(identifier))
        if not teacher:
            teacher = Teacher.query.filter(func.lower(Teacher.username) == identifier.lower()).first()
        if not teacher:
            teacher = Teacher.query.filter(func.lower(Teacher.email) == identifier.lower()).first()
        if not teacher:
            flash(f'Staff not found for "{identifier}".', 'danger')
            return redirect(url_for('teacher.teacher_portal'))
        if current_user.id == teacher.id:
            flash('Cannot remove self.', 'danger')
            return redirect(url_for('teacher.teacher_portal'))
        name = teacher.username; email = teacher.email
        try:
            delete_teacher_with_dependencies(teacher)
            db.session.commit()
        except ValueError as guard_err:
            db.session.rollback()
            flash(str(guard_err), 'warning')
            return redirect(url_for('teacher.teacher_portal'))
        except Exception as e:
            db.session.rollback()
            flash(f'Error: {e}', 'danger')
            return redirect(url_for('teacher.teacher_portal'))
        flash(f'{name} removed.', 'success')
        if not send_email(email, "Account Removed", f"Hi {name},\nAccount removed."):
            logger.warning(f"Post-removal email failed for {identifier} ({email})")
        return redirect(url_for('teacher.teacher_portal'))

    flash('Invalid person type. Use student or teacher.', 'danger')
    return redirect(url_for('teacher.teacher_portal'))


@teacher_bp.route("/create-subject", methods=['POST'])
@login_required
@require_role(['Professor', 'Admin'])
def create_subject():
    code = (request.form.get('subject_code') or '').strip().upper()
    name = (request.form.get('subject_name') or '').strip()
    academic_year = (request.form.get('academic_year') or '').strip()
    semester = (request.form.get('semester') or '').strip()
    default_session_type = normalize_session_type(request.form.get('default_session_type'))
    if not all([code, name, academic_year, semester]):
        flash('Code, Name, Academic Year, and Semester are required.', 'danger')
        return redirect(url_for('teacher.teacher_portal'))
    if Subject.query.filter_by(code=code, academic_year=academic_year, semester=semester).first():
        flash(f'Subject offering {code} already exists for {academic_year} {semester}.', 'danger')
        return redirect(url_for('teacher.teacher_portal'))
    try:
        new_subject = Subject(
            code=code,
            name=name,
            academic_year=academic_year,
            semester=semester,
            default_session_type=default_session_type
        )
        db.session.add(new_subject)
        db.session.commit()
        staff_link = SubjectStaff(
            teacher_id=current_user.id, subject_id=new_subject.id,
            role_in_subject='Professor', is_approved_by_prof=True
        )
        db.session.add(staff_link)
        db.session.commit()
        flash(f'Subject "{name}" created!', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error: {e}', 'danger')
    return redirect(url_for('teacher.teacher_portal'))


@teacher_bp.route("/add-ta-to-subject", methods=['POST'])
@login_required
@require_role(['Professor', 'Admin'])
def add_ta_to_subject():
    ta_id = request.form.get('ta_id')
    subject_id = request.form.get('subject_id')
    if not all([ta_id, subject_id]):
        flash('TA and Subject required.', 'danger')
        return redirect(url_for('teacher.teacher_portal'))
    prof_is_staff = SubjectStaff.query.filter_by(teacher_id=current_user.id, subject_id=subject_id, role_in_subject='Professor').first()
    if not prof_is_staff and current_user.role != 'Admin':
        flash('Unauthorized: You do not manage this subject.', 'danger')
        return redirect(url_for('teacher.teacher_portal'))
    subject = db.session.get(Subject, subject_id)
    if subject and subject.archived:
        flash('Subject is archived.', 'danger')
        return redirect(url_for('teacher.teacher_portal'))
    if SubjectStaff.query.filter_by(teacher_id=ta_id, subject_id=subject_id).first():
        flash('TA already assigned.', 'warning')
        return redirect(url_for('teacher.teacher_portal'))
    ta = db.session.get(Teacher, ta_id)
    if not (ta and ta.is_approved and ta.role == 'TA'):
        flash('Invalid or unapproved TA.', 'danger')
        return redirect(url_for('teacher.teacher_portal'))
    try:
        db.session.add(SubjectStaff(teacher_id=ta_id, subject_id=subject_id, role_in_subject='TA', is_approved_by_prof=False))
        db.session.commit()
        flash(f'TA {ta.username} requested for subject. Please approve.', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error: {e}', 'danger')
    return redirect(url_for('teacher.teacher_portal'))


@teacher_bp.route("/approve-ta/<int:staff_id>", methods=['POST'])
@login_required
@require_role(['Professor', 'Admin'])
def approve_ta(staff_id):
    staff_link = db.session.get(SubjectStaff, staff_id)
    if not staff_link:
        flash('Record not found.', 'danger')
        return redirect(url_for('teacher.teacher_portal'))
    prof_is_staff = SubjectStaff.query.filter_by(teacher_id=current_user.id, subject_id=staff_link.subject_id, role_in_subject='Professor').first()
    if not prof_is_staff and current_user.role != 'Admin':
        flash('Unauthorized: You do not manage this subject.', 'danger')
        return redirect(url_for('teacher.teacher_portal'))
    try:
        staff_link.is_approved_by_prof = True
        db.session.commit()
        flash(f'TA {staff_link.teacher.username} approved for {staff_link.subject.name}!', 'success')
        send_email_async(staff_link.teacher.email, "TA Assignment Approved",
                         f"Hi {staff_link.teacher.username},\nYour TA assignment for {staff_link.subject.name} was approved.")
    except Exception as e:
        db.session.rollback()
        flash(f'Error: {e}', 'danger')
    return redirect(url_for('teacher.teacher_portal'))


@teacher_bp.route("/remove-ta-from-subject/<int:staff_id>", methods=['POST'])
@login_required
@require_role(['Professor', 'Admin'])
def remove_ta_from_subject(staff_id):
    staff_link = db.session.get(SubjectStaff, staff_id)
    if not staff_link:
        flash('Record not found.', 'danger')
        return redirect(url_for('teacher.teacher_portal'))
    prof_is_staff = SubjectStaff.query.filter_by(teacher_id=current_user.id, subject_id=staff_link.subject_id, role_in_subject='Professor').first()
    if not prof_is_staff and current_user.role != 'Admin':
        flash('Unauthorized: You do not manage this subject.', 'danger')
        return redirect(url_for('teacher.teacher_portal'))
    if staff_link.role_in_subject == 'Professor':
        other_prof = SubjectStaff.query.filter(
            SubjectStaff.subject_id == staff_link.subject_id,
            SubjectStaff.role_in_subject == 'Professor',
            SubjectStaff.id != staff_link.id
        ).first()
        if not other_prof:
            flash('Cannot remove the only professor for this subject.', 'warning')
            return redirect(url_for('teacher.teacher_portal'))

    teacher_name = staff_link.teacher.username if staff_link.teacher else 'Unknown'
    teacher_email = staff_link.teacher.email if staff_link.teacher else None
    subject_name = staff_link.subject.name if staff_link.subject else 'Unknown subject'
    role_label = staff_link.role_in_subject
    was_pending = not staff_link.is_approved_by_prof

    try:
        db.session.delete(staff_link)
        db.session.commit()
        if role_label == 'Professor':
            flash(f'Professor {teacher_name} removed from {subject_name}.', 'success')
        elif was_pending:
            flash(f'TA request for {teacher_name} on {subject_name} denied.', 'success')
        else:
            flash(f'TA {teacher_name} removed from {subject_name}.', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error: {e}', 'danger')
        return redirect(url_for('teacher.teacher_portal'))

    if teacher_email:
        if not send_email(teacher_email, "Removed from Subject",
                          f"Hi {teacher_name},\nYour {role_label} assignment for {subject_name} was removed."):
            logger.warning(f"Post subject-removal email failed for staff_id={staff_id}")
    return redirect(url_for('teacher.teacher_portal'))


@teacher_bp.route("/archive-subject/<int:subject_id>", methods=['POST'])
@login_required
@require_role(['Professor', 'Admin'])
def archive_subject(subject_id):
    subject = db.session.get(Subject, subject_id)
    if not subject:
        flash('Subject not found.', 'danger')
        return redirect(url_for('teacher.teacher_portal'))
    prof_is_staff = SubjectStaff.query.filter_by(teacher_id=current_user.id, subject_id=subject_id, role_in_subject='Professor').first()
    if not prof_is_staff and current_user.role != 'Admin':
        flash('Unauthorized: You do not manage this subject.', 'danger')
        return redirect(url_for('teacher.teacher_portal'))
    try:
        subject.archived = True
        db.session.commit()
        flash(f'Subject "{subject.name}" archived.', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error archiving: {e}', 'danger')
    return redirect(url_for('teacher.teacher_portal'))


@teacher_bp.route("/unarchive-subject/<int:subject_id>", methods=['POST'])
@login_required
@require_role(['Professor', 'Admin'])
def unarchive_subject(subject_id):
    subject = db.session.get(Subject, subject_id)
    if not subject:
        flash('Subject not found.', 'danger')
        return redirect(url_for('teacher.teacher_portal'))
    prof_is_staff = SubjectStaff.query.filter_by(teacher_id=current_user.id, subject_id=subject_id, role_in_subject='Professor').first()
    if not prof_is_staff and current_user.role != 'Admin':
        flash('Unauthorized: You do not manage this subject.', 'danger')
        return redirect(url_for('teacher.teacher_portal'))
    try:
        subject.archived = False
        db.session.commit()
        flash(f'Subject "{subject.name}" unarchived.', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error unarchiving: {e}', 'danger')
    return redirect(url_for('teacher.teacher_portal'))


@teacher_bp.route("/unenroll-student-from-subject", methods=['POST'])
@login_required
@require_role(['Professor', 'Admin'])
def unenroll_student_from_subject():
    subject_id = request.form.get('subject_id')
    roll_number = (request.form.get('roll_number') or '').upper()
    if not subject_id or not roll_number:
        flash('Missing subject or student information.', 'danger')
        return redirect(url_for('teacher.teacher_portal'))
    prof_is_staff = SubjectStaff.query.filter_by(teacher_id=current_user.id, subject_id=subject_id, role_in_subject='Professor').first()
    if not prof_is_staff and current_user.role != 'Admin':
        flash('Unauthorized: You do not manage this subject.', 'danger')
        return redirect(url_for('teacher.teacher_portal'))
    student = db.session.get(Student, roll_number)
    subject = db.session.get(Subject, subject_id)
    if not student or not subject:
        flash('Student or Subject not found.', 'danger')
        return redirect(url_for('teacher.teacher_portal'))
    try:
        student.subjects.remove(subject)
        db.session.commit()
        flash(f"Student {student.name} ({student.roll_number}) unenrolled from {subject.name}.", 'success')
    except Exception as e:
        db.session.rollback()
        flash(f"An error occurred: {e}", 'danger')
    return redirect(url_for('teacher.teacher_portal'))


@teacher_bp.route("/change-professor", methods=['POST'])
@login_required
@require_role('Admin')
def change_professor():
    subject_id = request.form.get('subject_id')
    new_professor_id = request.form.get('new_professor_id')
    if not all([subject_id, new_professor_id]):
        flash('Missing subject or professor information.', 'danger')
        return redirect(url_for('teacher.teacher_portal'))
    try:
        subject = db.session.get(Subject, int(subject_id))
        new_prof = db.session.get(Teacher, int(new_professor_id))
        if not subject or not new_prof or new_prof.role != 'Professor':
            flash('Invalid subject or professor.', 'danger')
            return redirect(url_for('teacher.teacher_portal'))
        current_assignment = SubjectStaff.query.filter_by(subject_id=subject.id, role_in_subject='Professor').first()
        if current_assignment:
            if current_assignment.teacher_id == new_prof.id:
                flash(f'{new_prof.username} is already the professor for this subject.', 'info')
                return redirect(url_for('teacher.teacher_portal'))
            db.session.delete(current_assignment)
        existing = SubjectStaff.query.filter_by(teacher_id=new_prof.id, subject_id=subject.id).first()
        if existing:
            existing.role_in_subject = 'Professor'
            existing.is_approved_by_prof = True
        else:
            db.session.add(SubjectStaff(teacher_id=new_prof.id, subject_id=subject.id, role_in_subject='Professor', is_approved_by_prof=True))
        db.session.commit()
        flash(f'Professor for {subject.name} changed to {new_prof.username}.', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'An error occurred: {e}', 'danger')
    return redirect(url_for('teacher.teacher_portal'))


@teacher_bp.route("/upload-profile-photo", methods=['POST'])
@login_required
def upload_profile_photo():
    if not isinstance(current_user, Teacher):
        return jsonify({'error': 'Unauthorized'}), 401

    if 'profile_photo' not in request.files:
        return jsonify({'error': 'No file part'}), 400

    file = request.files['profile_photo']
    if file.filename == '':
        return jsonify({'error': 'No selected file'}), 400

    if not file.filename.lower().endswith(('.png', '.jpg', '.jpeg')):
        return jsonify({'error': 'Invalid file type. Only JPG and PNG are supported.'}), 400

    ext = os.path.splitext(file.filename)[1].lower()
    secure_name = secure_filename(file.filename)
    unique_filename = f"{uuid.uuid4().hex}_{secure_name}"
    
    upload_dir = os.path.join(current_app.static_folder, 'enrollment_uploads')
    os.makedirs(upload_dir, exist_ok=True)
    
    file_path = os.path.join(upload_dir, unique_filename)
    try:
        file.save(file_path)
    except Exception as e:
        logger.error(f"Error saving profile photo: {e}")
        return jsonify({'error': 'Failed to save photo'}), 500

    current_user.profile_photo_path = url_for('static', filename=f'enrollment_uploads/{unique_filename}')
    db.session.commit()

    return jsonify({
        'message': 'Profile photo updated successfully',
        'profile_photo_url': current_user.profile_photo_url
    })
