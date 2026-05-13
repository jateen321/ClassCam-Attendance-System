"""
routes/auth.py — Auth Blueprint
================================
Covers: teacher login, teacher registration, password reset (request + confirm).

Blueprint name: 'auth'
url_for prefix: url_for('auth.teacher_login'), etc.
"""

import random
from datetime import datetime, timedelta, timezone
from flask import Blueprint, request, redirect, url_for, flash, session, jsonify
from flask_login import login_user
from sqlalchemy import func
from app.extensions import db
from app.models import Teacher
from app.utils.email import send_email, send_email_async

auth_bp = Blueprint('auth', __name__)


def _normalize_teacher_identifier(raw_identifier):
    return (raw_identifier or '').strip()


def _find_teacher_by_username(raw_identifier):
    normalized_username = _normalize_teacher_identifier(raw_identifier)
    if not normalized_username:
        return None, normalized_username

    teacher = Teacher.query.filter(func.lower(Teacher.username) == normalized_username.lower()).first()
    if teacher:
        return teacher, normalized_username

    teacher = Teacher.query.filter(
        func.lower(func.trim(Teacher.username)) == normalized_username.lower()
    ).first()
    return teacher, normalized_username


@auth_bp.route("/login", methods=['POST'])
def teacher_login():
    teacher, normalized_username = _find_teacher_by_username(request.form.get('username'))
    password = request.form.get('password')
    if not normalized_username or not password:
        flash('Invalid credentials.', 'danger')
        return redirect(url_for('teacher.teacher_portal'))

    if teacher and teacher.check_password(password):
        if not teacher.is_approved:
            flash('Account awaiting Admin approval.', 'warning')
            return redirect(url_for('teacher.teacher_portal'))
        login_user(teacher)
        session['user_type'] = 'teacher'
        flash(f'Login successful! Welcome {teacher.role} {teacher.username}.', 'success')
        return redirect(url_for('teacher.teacher_portal'))
    flash('Invalid credentials.', 'danger')
    return redirect(url_for('teacher.teacher_portal'))


@auth_bp.route("/teacher-register", methods=['POST'])
def teacher_register():
    try:
        username = _normalize_teacher_identifier(request.form.get('reg_username'))
        email = (request.form.get('reg_email') or '').strip().lower()
        password = request.form.get('reg_password')
        role = request.form.get('reg_role')

        if not all([username, email, password, role]):
            flash('All fields required.', 'danger')
            return redirect(url_for('teacher.teacher_portal'))
        if role not in ['Professor', 'TA']:
            flash('Invalid role.', 'danger')
            return redirect(url_for('teacher.teacher_portal'))
        if len(password) < 6:
            flash('Password too short.', 'danger')
            return redirect(url_for('teacher.teacher_portal'))
        if not email.lower().endswith('@iitj.ac.in'):
            flash('Invalid email domain. Only @iitj.ac.in emails are allowed.', 'danger')
            return redirect(url_for('teacher.teacher_portal'))
        if Teacher.query.filter(func.lower(func.trim(Teacher.username)) == username.lower()).first():
            flash('Username exists.', 'danger')
            return redirect(url_for('teacher.teacher_portal'))
        if Teacher.query.filter(func.lower(Teacher.email) == email.lower()).first():
            flash('Email exists.', 'danger')
            return redirect(url_for('teacher.teacher_portal'))

        new_teacher = Teacher(username=username, email=email, role=role, is_approved=False)
        new_teacher.set_password(password)
        db.session.add(new_teacher)
        db.session.commit()

        admins = Teacher.query.filter_by(role='Admin', is_approved=True).all()
        admin_emails = [a.email for a in admins if a.email]
        if admin_emails:
            send_email_async(admin_emails, "New Staff Request",
                             f"User: {username}\nRole: {role}\nEmail: {email}\nApprove in portal.")

        flash('Request submitted. Awaiting Admin approval.', 'success')
        return redirect(url_for('teacher.teacher_portal'))

    except Exception as e:
        db.session.rollback()
        flash(f'Error: {e}', 'danger')
        return redirect(url_for('teacher.teacher_portal'))


@auth_bp.route("/teacher-request-password-reset", methods=['POST'])
def teacher_request_password_reset():
    try:
        email = (request.form.get('email') or '').strip().lower()
        if not email:
            return jsonify({'error': 'Email required.'}), 400
        if not email.lower().endswith('@iitj.ac.in'):
            return jsonify({'error': 'Invalid email domain.'}), 400

        teacher = Teacher.query.filter(func.lower(Teacher.email) == email.lower()).first()
        if not teacher:
            return jsonify({'message': 'If an account with that email exists, a reset code has been sent.'})

        otp = str(random.randint(100000, 999999))
        teacher.otp = otp
        teacher.otp_generated_at = datetime.now(timezone.utc)

        if not send_email(teacher.email, "Teacher Password Reset Code",
                          f"Hi {teacher.username},\nYour password reset code is: {otp}\nIt expires in 10 minutes."):
            db.session.rollback()
            return jsonify({'error': 'Failed to send reset code email.'}), 500
        db.session.commit()
        return jsonify({'message': f'Reset code sent to {teacher.email}.'})

    except Exception as e:
        db.session.rollback()
        return jsonify({'error': 'Internal error during reset request.'}), 500


@auth_bp.route("/teacher-reset-password", methods=['POST'])
def teacher_reset_password():
    try:
        email = (request.form.get('email') or '').strip().lower()
        otp_attempt = request.form.get('otp')
        new_pwd = request.form.get('new_password')

        if not all([email, otp_attempt, new_pwd]):
            return jsonify({'error': 'All fields required.'}), 400
        if len(new_pwd) < 6:
            return jsonify({'error': 'Password too short (min 6 characters).'}), 400

        teacher = Teacher.query.filter(func.lower(Teacher.email) == email.lower()).first()
        if not teacher:
            return jsonify({'error': 'Invalid email or OTP.'}), 400
        if teacher.otp is None or teacher.otp_generated_at is None:
            return jsonify({'error': 'Invalid or expired OTP.'}), 400
        if teacher.otp != otp_attempt:
            return jsonify({'error': 'Invalid OTP provided.'}), 400
        if (datetime.now(timezone.utc) - teacher.otp_generated_at) > timedelta(minutes=10):
            teacher.otp = None
            teacher.otp_generated_at = None
            db.session.commit()
            return jsonify({'error': 'OTP expired.'}), 400

        teacher.set_password(new_pwd)
        teacher.otp = None
        teacher.otp_generated_at = None
        db.session.commit()
        return jsonify({'message': 'Password reset successful! You can now log in.'})

    except Exception as e:
        db.session.rollback()
        return jsonify({'error': 'Internal error during password reset.'}), 500
