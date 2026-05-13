"""
utils/decorators.py — Route Guard Decorators
==============================================
Custom decorators that restrict route access by user role/type.
IMPORTS FROM: extensions.py (indirectly via models), flask_login
"""

from functools import wraps
from flask import flash, redirect, url_for, request, jsonify
from flask_login import current_user


def require_role(roles):
    """
    Restrict a route to Teachers with specific role(s).
    Usage:
        @require_role('Admin')
        @require_role(['Professor', 'Admin'])
    """
    if isinstance(roles, str):
        roles = [roles]

    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            # Import here to avoid circular imports at module load time
            from app.models import Teacher, Student

            if not isinstance(current_user, Teacher):
                flash('Unauthorized.', 'danger')
                if isinstance(current_user, Student):
                    return redirect(url_for('student.student_portal'))
                return redirect(url_for('teacher.teacher_portal'))

            if current_user.role not in roles:
                flash('Unauthorized.', 'danger')
                return redirect(url_for('teacher.teacher_portal'))

            return f(*args, **kwargs)
        return decorated_function
    return decorator


def require_student(f):
    """
    Restrict a route to authenticated Students only.
    Usage:
        @require_student
    """
    @wraps(f)
    def decorated_function(*args, **kwargs):
        from app.models import Teacher, Student

        accept_header = (request.headers.get('Accept') or '').lower()
        wants_json = request.path.startswith('/student-bounding-box/') or 'application/json' in accept_header
        if not isinstance(current_user, Student):
            if wants_json:
                return jsonify({'error': 'Unauthorized.'}), 403
            flash('Unauthorized.', 'danger')
            if isinstance(current_user, Teacher):
                return redirect(url_for('teacher.teacher_portal'))
            return redirect(url_for('student.student_portal'))
        return f(*args, **kwargs)
    return decorated_function
