"""
utils/db_helpers.py — Database Setup & Helper Functions
=========================================================
Handles DB connection, schema setup, default admin seeding,
and teacher-removal guard logic.

IMPORTS FROM: extensions.py, models.py (both safe — no circular risk)
"""

import os
import logging
import time
from sqlalchemy import func, text
import psycopg2

logger = logging.getLogger(__name__)

# ── Read DB credentials from environment ────────────────────────────────────
DB_NAME = os.environ.get('DB_NAME', 'attendance_db')
DB_USER = os.environ.get('DB_USER', 'projectuser')
DB_PASS = os.environ.get('DB_PASS', 'projectpass')
DB_HOST = os.environ.get('DB_HOST', 'localhost')
DB_PORT = os.environ.get('DB_PORT', '5432')

DEFAULT_ADMIN_EMAIL    = os.environ.get('DEFAULT_ADMIN_EMAIL')
DEFAULT_ADMIN_PASSWORD = os.environ.get('DEFAULT_ADMIN_PASSWORD')
DEFAULT_ADMIN_USERNAME = os.environ.get('DEFAULT_ADMIN_USERNAME') or (
    DEFAULT_ADMIN_EMAIL.split('@')[0] if DEFAULT_ADMIN_EMAIL else 'admin'
)


def _int_env(name, default):
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _float_env(name, default):
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def get_db_connection():
    """Return a raw psycopg2 connection (used for bulk INSERT with execute_batch)."""
    try:
        return psycopg2.connect(
            dbname=DB_NAME, user=DB_USER, password=DB_PASS,
            host=DB_HOST, port=DB_PORT
        )
    except psycopg2.OperationalError as e:
        logger.error(f"DB Connect Error: {e}")
        return None


def wait_for_database(max_attempts=None, base_delay=None, max_delay=15.0):
    """
    Block until Postgres accepts a connection, or give up after max_attempts.

    In Docker the DB container may still be starting when we run, so retry with
    exponential backoff instead of crashing on the first refused connection.
    Returns True once connected, False if every attempt failed.

    Tunable via DB_WAIT_ATTEMPTS / DB_WAIT_DELAY for slower hosts.
    """
    if max_attempts is None:
        max_attempts = _int_env('DB_WAIT_ATTEMPTS', 10)
    if base_delay is None:
        base_delay = _float_env('DB_WAIT_DELAY', 1.0)

    delay = base_delay
    for attempt in range(1, max_attempts + 1):
        conn = get_db_connection()
        if conn is not None:
            conn.close()
            logger.info(f"Database reachable on attempt {attempt}/{max_attempts}.")
            return True
        if attempt == max_attempts:
            break
        logger.warning(
            f"Database not ready (attempt {attempt}/{max_attempts}); retrying in {delay:.0f}s."
        )
        time.sleep(delay)
        delay = min(delay * 2, max_delay)

    logger.error(f"Database unreachable after {max_attempts} attempts.")
    return False


def setup_database(app):
    """Create all tables and run any necessary migrations."""
    from app.extensions import db
    with app.app_context():
        try:
            # --- PRE-MIGRATION: Ensure 'attendancestatus' Enum type has correct lowercase values ---
            # This MUST run before db.create_all() so SQLAlchemy uses our type, not its auto-generated one.
            try:
                db.session.execute(text("""
                    DO $$ 
                    DECLARE
                        col_type TEXT;
                        enum_values TEXT[];
                    BEGIN
                        -- Check if the enum type exists and what values it has
                        SELECT array_agg(enumlabel::TEXT) INTO enum_values
                        FROM pg_enum
                        JOIN pg_type ON pg_enum.enumtypid = pg_type.oid
                        WHERE pg_type.typname = 'attendancestatus';

                        IF enum_values IS NULL THEN
                            -- Type doesn't exist, create fresh with lowercase values
                            CREATE TYPE attendancestatus AS ENUM ('present', 'absent', 'medical_leave', 'other_leave');
                        ELSIF 'PRESENT' = ANY(enum_values) THEN
                            -- Stale UPPERCASE type exists — need to drop and recreate with lowercase
                            -- First convert the column to TEXT so we can drop the type
                            SELECT data_type INTO col_type FROM information_schema.columns
                            WHERE table_name = 'attendance_records' AND column_name = 'status';
                            
                            IF col_type = 'USER-DEFINED' THEN
                                ALTER TABLE attendance_records ALTER COLUMN status TYPE TEXT;
                            END IF;
                            
                            DROP TYPE attendancestatus;
                            CREATE TYPE attendancestatus AS ENUM ('present', 'absent', 'medical_leave', 'other_leave');
                        ELSE
                            -- Type exists with lowercase values, just add missing ones
                            BEGIN ALTER TYPE attendancestatus ADD VALUE IF NOT EXISTS 'medical_leave'; EXCEPTION WHEN others THEN NULL; END;
                            BEGIN ALTER TYPE attendancestatus ADD VALUE IF NOT EXISTS 'other_leave'; EXCEPTION WHEN others THEN NULL; END;
                        END IF;
                    END $$;
                """))
                db.session.commit()

                # If the column is still plain text, cast it to the ENUM now
                res = db.session.execute(text("""
                    SELECT data_type FROM information_schema.columns 
                    WHERE table_name = 'attendance_records' AND column_name = 'status'
                """)).fetchone()
                if res and res[0] != 'USER-DEFINED':
                    db.session.execute(text("ALTER TABLE attendance_records ALTER COLUMN status TYPE TEXT"))
                    db.session.execute(text("ALTER TABLE attendance_records ALTER COLUMN status TYPE attendancestatus USING LOWER(status)::attendancestatus"))
                    db.session.commit()
                    logger.info("Enum migration: 'status' column successfully converted to attendancestatus type.")
            except Exception as e:
                db.session.rollback()
                logger.warning(f"Pre-migration warning (Enum): {e}")

            db.create_all()
            try:
                # Ensure teacher OTP columns exist for password reset flows.
                db.session.execute(text("ALTER TABLE teachers ADD COLUMN IF NOT EXISTS otp VARCHAR(6)"))
                db.session.execute(text("ALTER TABLE teachers ADD COLUMN IF NOT EXISTS otp_generated_at TIMESTAMPTZ"))
                db.session.execute(text("ALTER TABLE teachers ADD COLUMN IF NOT EXISTS profile_photo_path VARCHAR(255)"))
                db.session.execute(text("ALTER TABLE students ADD COLUMN IF NOT EXISTS profile_photo_path VARCHAR(255)"))

                # Attendance rows can now distinguish photo-derived vs manual entries.
                db.session.execute(text("ALTER TABLE attendance_records ADD COLUMN IF NOT EXISTS source VARCHAR(20)"))
                db.session.execute(text("ALTER TABLE attendance_records ALTER COLUMN source SET DEFAULT 'photo'"))

                # Subject offerings are now term-aware.
                db.session.execute(text("ALTER TABLE subjects ADD COLUMN IF NOT EXISTS academic_year VARCHAR(20)"))
                db.session.execute(text("ALTER TABLE subjects ADD COLUMN IF NOT EXISTS semester VARCHAR(20)"))
                db.session.execute(text("ALTER TABLE subjects ADD COLUMN IF NOT EXISTS default_session_type VARCHAR(20)"))
                db.session.execute(text("UPDATE subjects SET academic_year = COALESCE(NULLIF(academic_year, ''), 'Legacy')"))
                db.session.execute(text("UPDATE subjects SET semester = COALESCE(NULLIF(semester, ''), 'Legacy')"))
                db.session.execute(text("UPDATE subjects SET default_session_type = COALESCE(NULLIF(default_session_type, ''), 'class')"))
                db.session.execute(text("ALTER TABLE subjects ALTER COLUMN academic_year SET DEFAULT 'Legacy'"))
                db.session.execute(text("ALTER TABLE subjects ALTER COLUMN semester SET DEFAULT 'Legacy'"))
                db.session.execute(text("ALTER TABLE subjects ALTER COLUMN default_session_type SET DEFAULT 'class'"))
                db.session.execute(text("ALTER TABLE subjects ALTER COLUMN academic_year SET NOT NULL"))
                db.session.execute(text("ALTER TABLE subjects ALTER COLUMN semester SET NOT NULL"))
                db.session.execute(text("ALTER TABLE subjects ALTER COLUMN default_session_type SET NOT NULL"))
                db.session.execute(text("ALTER TABLE subjects DROP CONSTRAINT IF EXISTS subjects_code_key"))
                db.session.execute(text("""
                    DO $$
                    BEGIN
                        IF NOT EXISTS (
                            SELECT 1
                            FROM pg_constraint
                            WHERE conname = '_subject_term_uc'
                        ) THEN
                            ALTER TABLE subjects
                            ADD CONSTRAINT _subject_term_uc UNIQUE (code, academic_year, semester);
                        END IF;
                    END $$;
                """))

                # Keep new photo metadata columns resilient in iterative local runs.
                db.session.execute(text("ALTER TABLE photos ADD COLUMN IF NOT EXISTS image_width INTEGER"))
                db.session.execute(text("ALTER TABLE photos ADD COLUMN IF NOT EXISTS image_height INTEGER"))
                db.session.execute(text("ALTER TABLE photos ADD COLUMN IF NOT EXISTS session_type VARCHAR(20)"))
                db.session.execute(text("UPDATE photos SET session_type = COALESCE(NULLIF(session_type, ''), 'class')"))
                db.session.execute(text("ALTER TABLE photos ALTER COLUMN session_type SET DEFAULT 'class'"))

                db.session.execute(text("ALTER TABLE bounding_boxes ADD COLUMN IF NOT EXISTS review_status VARCHAR(20)"))
                db.session.execute(text("ALTER TABLE bounding_boxes ADD COLUMN IF NOT EXISTS is_active BOOLEAN"))
                db.session.execute(text("ALTER TABLE bounding_boxes ADD COLUMN IF NOT EXISTS created_by_student_roll_number VARCHAR(80)"))
                db.session.execute(text("ALTER TABLE bounding_boxes ADD COLUMN IF NOT EXISTS reviewed_by_teacher_id INTEGER"))
                db.session.execute(text("ALTER TABLE bounding_boxes ADD COLUMN IF NOT EXISTS request_group_id VARCHAR(64)"))
                db.session.execute(text("ALTER TABLE bounding_boxes ADD COLUMN IF NOT EXISTS source_box_id INTEGER"))
                db.session.execute(text("ALTER TABLE bounding_boxes ADD COLUMN IF NOT EXISTS reviewed_at TIMESTAMPTZ"))
                db.session.execute(text("UPDATE bounding_boxes SET review_status = COALESCE(NULLIF(review_status, ''), 'pending')"))
                db.session.execute(text("UPDATE bounding_boxes SET is_active = COALESCE(is_active, true)"))

                db.session.commit()
            except Exception as migrate_err:
                db.session.rollback()
                logger.warning(f"DB migration warning: {migrate_err}")
            logger.info("Database setup complete.")
        except Exception as e:
            db.session.rollback()
            logger.error(f"Error during database setup: {e}")


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
