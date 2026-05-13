"""
app/__init__.py — Application Factory
===========================================
Creates and configures the Flask application instance.

Registers all Blueprints, sets up Flask-Login (user loader and
unauthorized handler), and initializes database extensions.
"""

from flask import Flask, request, redirect, url_for, flash, jsonify
from flask_wtf.csrf import CSRFError
from app.extensions import db, login_manager, csrf
from app.models import Teacher, Student
from config import Config
import os
from werkzeug.middleware.proxy_fix import ProxyFix


def create_app(config_class=Config):
    app = Flask(__name__,
                template_folder='../templates',
                static_folder='../static')
    app.config.from_object(config_class)

    # ── Step 1: Connect extensions to the app ──────────────────────────────
    # This is the key step — extensions were created in extensions.py with NO app.
    # Now we connect them via init_app(). This is what breaks the circular import.
    db.init_app(app)
    csrf.init_app(app)
    login_manager.init_app(app)
    login_manager.login_view = 'teacher.teacher_portal'
    login_manager.login_message_category = 'info'

    # Trust headers from Nginx (1 proxy layer in front)
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)

    # ── Step 2: Ensure upload directories exist ────────────────────────────
    for subdir in ('annotated_uploads', 'attendance_raw_uploads', 'enrollment_uploads'):
        os.makedirs(os.path.join(app.static_folder, subdir), exist_ok=True)

    # ── Step 3: Register Blueprints ────────────────────────────────────────
    # Imported INSIDE the function to avoid circular imports.
    # At this point, db is already initialized so models can be safely imported.
    from app.routes.main import main_bp
    from app.routes.auth import auth_bp
    from app.routes.teacher import teacher_bp
    from app.routes.student import student_bp
    from app.routes.attendance import attendance_bp

    app.register_blueprint(main_bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(teacher_bp)
    app.register_blueprint(student_bp)
    app.register_blueprint(attendance_bp)

    def _wants_json_error():
        # API endpoints in this app use form-data POSTs + fetch; make failures JSON-safe.
        accept_header = (request.headers.get('Accept') or '').lower()
        if request.path.startswith('/student-bounding-box/'):
            return True
        if request.path.startswith('/review-bounding-box/'):
            return True
        return 'application/json' in accept_header

    @app.errorhandler(CSRFError)
    def handle_csrf_error(error):
        if _wants_json_error():
            return jsonify({
                'error': 'Security token expired. Refresh the page and try again.',
                'code': 'csrf_failed'
            }), 400
        return (
            "<!doctype html><html lang=en><title>400 Bad Request</title>"
            f"<h1>Bad Request</h1><p>{error.description}</p></html>",
            400,
        )

    # ── Step 4: Flask-Login hooks ──────────────────────────────────────────
    from flask import session

    @login_manager.user_loader
    def load_user(user_id):
        if session.get('user_type') == 'teacher':
            return db.session.get(Teacher, int(user_id))
        elif session.get('user_type') == 'student':
            return db.session.get(Student, str(user_id))
        return None

    @login_manager.unauthorized_handler
    def unauthorized():
        if _wants_json_error():
            return jsonify({'error': 'Login required to access this resource.'}), 401
        flash('Login required to access this page.', 'warning')
        endpoint = request.endpoint or ''
        if 'teacher' in endpoint or 'approve_teacher' in endpoint:
            return redirect(url_for('teacher.teacher_portal'))
        if 'student' in endpoint:
            return redirect(url_for('student.student_portal'))
        return redirect(url_for('main.index'))

    return app
