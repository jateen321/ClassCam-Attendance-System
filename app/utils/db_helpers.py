"""
utils/db_helpers.py — Database Setup & Helper Functions
=========================================================
Handles default admin seeding and teacher-removal guard logic.

IMPORTS FROM: extensions.py, models.py (both safe — no circular risk)
"""

import logging
from sqlalchemy import func

logger = logging.getLogger(__name__)

# ── Read default-admin credentials from environment ─────────────────────────
import os

DEFAULT_ADMIN_EMAIL = os.environ.get('DEFAULT_ADMIN_EMAIL')
DEFAULT_ADMIN_PASSWORD = os.environ.get('DEFAULT_ADMIN_PASSWORD')
DEFAULT_ADMIN_USERNAME = os.environ.get('DEFAULT_ADMIN_USERNAME') or (
    DEFAULT_ADMIN_EMAIL.split('@')[0] if DEFAULT_ADMIN_EMAIL else 'admin'
)


def ensure_default_admin(app):
    """Create or synchronize the configured default Admin account."""
    from app.extensions import db
    from app.models import Teacher

    email    = DEFAULT_ADMIN_EMAIL
    password = DEFAULT_ADMIN_PASSWORD
    username = DEFAULT_ADMIN_USERNAME

    if not email or not password or not username:
        logger.info("Default admin seed skipped: missing credentials.")
        return

    with app.app_context():
        try:
            desired_email = email.strip().lower()
            desired_username = username.strip()

            candidates = Teacher.query.filter(
                (func.lower(Teacher.email) == desired_email) |
                (func.lower(Teacher.username) == desired_username.lower())
            ).order_by(Teacher.id).all()

            admin = next(
                (teacher for teacher in candidates if teacher.email.lower() == desired_email),
                candidates[0] if candidates else None
            )

            if admin is None:
                admin = Teacher(username=desired_username, email=email, role='Admin', is_approved=True)
                db.session.add(admin)
                action = "Created"
            else:
                action = "Synchronized"

            admin.username = desired_username
            admin.email = email
            admin.role = 'Admin'
            admin.is_approved = True
            admin.otp = None
            admin.otp_generated_at = None
            admin.set_password(password)

            for stale_teacher in candidates:
                if stale_teacher.id != admin.id:
                    db.session.delete(stale_teacher)

            db.session.commit()
            logger.info(f"{action} default admin '{email}'.")
        except Exception as e:
            db.session.rollback()
            logger.error(f"Default admin seed failed: {e}")
            raise


# ── Teacher removal guard helpers ────────────────────────────────────────────
def _subjects_left_without_professor(teacher_id):
    """Return subjects where this teacher is the only professor."""
    from app.models import SubjectStaff
    prof_links = SubjectStaff.query.filter_by(teacher_id=teacher_id, role_in_subject='Professor').all()
    if not prof_links:
        return []

    subjects_without_prof = []
    for link in prof_links:
        other_prof = SubjectStaff.query.filter(
            SubjectStaff.subject_id == link.subject_id,
            SubjectStaff.role_in_subject == 'Professor',
            SubjectStaff.teacher_id != teacher_id
        ).first()
        if not other_prof and link.subject:
            subjects_without_prof.append(link.subject)

    # Deduplicate while preserving stable order
    seen = set()
    unique = []
    for s in subjects_without_prof:
        if s.id not in seen:
            seen.add(s.id)
            unique.append(s)
    return unique


def _guard_teacher_removal(teacher):
    """Raise ValueError if removal would leave the system in an unsafe state."""
    from app.models import Teacher, SubjectStaff
    if teacher.role == 'Admin':
        approved_admin_count = Teacher.query.filter_by(role='Admin', is_approved=True).count()
        if approved_admin_count <= 1:
            raise ValueError("Cannot remove the last approved Admin.")

    subjects_without_prof = _subjects_left_without_professor(teacher.id)
    if subjects_without_prof:
        labels = ", ".join([f"{s.code} ({s.name})" for s in subjects_without_prof])
        raise ValueError(
            f"Cannot remove {teacher.username}: they are the only professor for {labels}. "
            "Change the professor first."
        )


def delete_teacher_with_dependencies(teacher):
    """Remove staff links then delete the teacher — prevents FK violations."""
    from app.extensions import db
    from app.models import SubjectStaff
    _guard_teacher_removal(teacher)
    for link in SubjectStaff.query.filter_by(teacher_id=teacher.id).all():
        db.session.delete(link)
    db.session.flush()
    db.session.delete(teacher)
