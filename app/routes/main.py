"""
routes/main.py — Main Blueprint
================================
Covers: homepage, logout, unified change-password,
        and Flask-Login hooks (user_loader, unauthorized_handler).

Blueprint name: 'main'
url_for prefix: url_for('main.index'), url_for('main.logout'), etc.
"""

from flask import Blueprint, render_template, redirect, url_for, flash, request, session, jsonify
from flask_login import login_required, logout_user, current_user
from app.extensions import db

main_bp = Blueprint('main', __name__)


# ── Flask-Login Hooks ────────────────────────────────────────────────────────
# These must be registered on login_manager, which is done in create_app()
# via main_bp — see app/__init__.py


# ── Routes ───────────────────────────────────────────────────────────────────
@main_bp.route("/")
def index():
    return render_template('index.html')


@main_bp.route("/logout")
@login_required
def logout():
    user_type = session.get('user_type', 'guest')
    logout_user()
    session.clear()
    flash('Logged out.', 'success')
    if user_type == 'teacher':
        return redirect(url_for('teacher.teacher_portal'))
    if user_type == 'student':
        return redirect(url_for('student.student_portal'))
    return redirect(url_for('main.index'))


@main_bp.route("/change-password", methods=['POST'])
@login_required
def change_password():
    """Unified password change for both Teacher and Student."""
    from app.models import Teacher, Student
    try:
        current_pwd = request.form.get('current_password')
        new_pwd = request.form.get('new_password')

        if not all([current_pwd, new_pwd]):
            return jsonify({'error': 'All fields required.'}), 400
        if len(new_pwd) < 6:
            return jsonify({'error': 'Password must be at least 6 characters.'}), 400

        if isinstance(current_user, Student):
            user = db.session.get(Student, current_user.roll_number)
        elif isinstance(current_user, Teacher):
            user = db.session.get(Teacher, current_user.id)
        else:
            return jsonify({'error': 'Invalid user type.'}), 400

        if not user:
            logout_user()
            return jsonify({'error': 'User not found.'}), 404
        if not user.check_password(current_pwd):
            return jsonify({'error': 'Current password is incorrect.'}), 401
        if current_pwd == new_pwd:
            return jsonify({'error': 'New password must be different.'}), 400

        user.set_password(new_pwd)
        db.session.commit()
        logout_user()
        return jsonify({'message': 'Password changed successfully! You have been logged out.'})

    except Exception as e:
        db.session.rollback()
        return jsonify({'error': 'Internal error.'}), 500
