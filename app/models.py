"""
models.py — Database Tables (SQLAlchemy Models)
================================================
WHY HERE:
  Models only need 'db' from extensions.py — no Flask app, no routes.
  This keeps the database schema isolated and importable from anywhere.

IMPORT RULE:
  models.py → imports from: extensions.py ONLY
  Nothing else imports models.py at module level (only inside functions or factory).
"""

from app.extensions import db
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy import UniqueConstraint
import enum


# ── Association Table: Students ↔ Subjects ─────────────────────────────────
# Not a class — just a plain join table for the many-to-many relationship
enrollments = db.Table(
    'enrollments',
    db.Column('student_roll_number', db.String(80),
              db.ForeignKey('students.roll_number', ondelete='CASCADE'),
              primary_key=True),
    db.Column('subject_id', db.Integer,
              db.ForeignKey('subjects.id', ondelete='CASCADE'),
              primary_key=True)
)


# ── Association Table: Teachers ↔ Subjects (with role) ─────────────────────
class SubjectStaff(db.Model):
    __tablename__ = 'subject_staff'
    id = db.Column(db.Integer, primary_key=True)
    teacher_id = db.Column(db.Integer, db.ForeignKey('teachers.id', ondelete='CASCADE'), nullable=False)
    subject_id = db.Column(db.Integer, db.ForeignKey('subjects.id', ondelete='CASCADE'), nullable=False)
    role_in_subject = db.Column(db.String(50), nullable=False, default='TA')  # 'Professor' or 'TA'
    is_approved_by_prof = db.Column(db.Boolean, default=False, nullable=False)

    __table_args__ = (
        db.UniqueConstraint('teacher_id', 'subject_id', name='_teacher_subject_uc'),
    )

    # ORM-level cascades so deleting a teacher/subject deletes link rows
    teacher = db.relationship('Teacher', backref=db.backref('staff_assignments', cascade='all, delete-orphan'))
    subject = db.relationship('Subject', backref=db.backref('staff_assignments', cascade='all, delete-orphan'))


# ── Teacher Model ───────────────────────────────────────────────────────────
class Teacher(UserMixin, db.Model):
    __tablename__ = 'teachers'
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    otp = db.Column(db.String(6), nullable=True)
    otp_generated_at = db.Column(db.DateTime(timezone=True), nullable=True)
    role = db.Column(db.String(50), nullable=False, default='TA')  # 'Admin', 'Professor', 'TA'
    is_approved = db.Column(db.Boolean, default=False, nullable=False)

    # Preserve legacy and new photo audit trails when a teacher is removed
    annotated_photos = db.relationship('AnnotatedPhoto', backref='uploader', lazy=True)
    uploaded_photos = db.relationship('Photo', backref='uploader', lazy=True)
    reviewed_bounding_boxes = db.relationship(
        'BoundingBox',
        foreign_keys='BoundingBox.reviewed_by_teacher_id',
        backref='reviewed_by',
        lazy=True
    )
    profile_photo_path = db.Column(db.String(255), nullable=True)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    @property
    def profile_photo_url(self):
        if not self.profile_photo_path:
            return None
        return self.profile_photo_path


# ── Student Model ───────────────────────────────────────────────────────────
class Student(UserMixin, db.Model):
    __tablename__ = 'students'
    roll_number = db.Column(db.String(80), primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=True)
    face_encodings = db.Column(JSONB, nullable=True)
    is_verified = db.Column(db.Boolean, default=False, nullable=False)
    enrollment_data_path = db.Column(db.String(255), nullable=True)
    profile_photo_path = db.Column(db.String(255), nullable=True)
    otp = db.Column(db.String(6), nullable=True)
    otp_generated_at = db.Column(db.DateTime(timezone=True), nullable=True)

    subjects = db.relationship(
        'Subject', secondary=enrollments, lazy='dynamic',
        backref=db.backref('students', lazy='dynamic')
    )
    attendance_records = db.relationship(
        'AttendanceRecord', backref='student', lazy=True, cascade="all, delete-orphan"
    )
    bounding_boxes = db.relationship(
        'BoundingBox',
        foreign_keys='BoundingBox.student_roll_number',
        backref='box_student',
        lazy=True,
        cascade="all, delete-orphan"
    )
    submitted_box_requests = db.relationship(
        'BoundingBox',
        foreign_keys='BoundingBox.created_by_student_roll_number',
        backref='requested_by_student',
        lazy=True
    )

    def get_id(self):
        return self.roll_number

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        if not self.password_hash:
            return False
        return check_password_hash(self.password_hash, password)

    @property
    def profile_photo_url(self):
        if self.profile_photo_path:
            return self.profile_photo_path
        if not getattr(self, 'is_verified', False) or not self.enrollment_data_path:
            return None
        if self.enrollment_data_path.lower().endswith(('.png', '.jpg', '.jpeg', '.gif')):
            return self.enrollment_data_path
        base_path = self.enrollment_data_path.rsplit('.', 1)[0]
        return f"{base_path}_thumb.jpg"


# ── Subject Model ───────────────────────────────────────────────────────────
class Subject(db.Model):
    __tablename__ = 'subjects'
    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(50), nullable=False)
    name = db.Column(db.String(150), nullable=False)
    academic_year = db.Column(db.String(20), nullable=False, default='Legacy')
    semester = db.Column(db.String(20), nullable=False, default='Legacy')
    default_session_type = db.Column(db.String(20), nullable=False, default='class')
    archived = db.Column(db.Boolean, default=False, nullable=False)

    __table_args__ = (
        UniqueConstraint('code', 'academic_year', 'semester', name='_subject_term_uc'),
    )

    attendance_records = db.relationship(
        'AttendanceRecord', backref='subject', lazy=True, cascade="all, delete-orphan"
    )
    annotated_photos = db.relationship(
        'AnnotatedPhoto', backref='subject', lazy=True, cascade="all, delete-orphan"
    )
    photos = db.relationship(
        'Photo', backref='subject', lazy=True, cascade="all, delete-orphan"
    )


# ── AttendanceStatus Enum ──────────────────────────────────────────────────
class AttendanceStatus(str, enum.Enum):
    PRESENT = 'present'
    ABSENT = 'absent'
    MEDICAL_LEAVE = 'medical_leave'
    OTHER_LEAVE = 'other_leave'


# ── AttendanceRecord Model ──────────────────────────────────────────────────
class AttendanceRecord(db.Model):
    __tablename__ = 'attendance_records'
    id = db.Column(db.Integer, primary_key=True)
    subject_id = db.Column(db.Integer, db.ForeignKey('subjects.id', ondelete='CASCADE'), nullable=False)
    student_roll_number = db.Column(db.String(80), db.ForeignKey('students.roll_number', ondelete='CASCADE'), nullable=False)
    attendance_date = db.Column(db.Date, nullable=False)
    # values_callable tells SQLAlchemy to use .value ('present', 'absent', etc.)
    # instead of .name ('PRESENT', 'ABSENT', etc.) for the PostgreSQL ENUM type
    status = db.Column(
        db.Enum(AttendanceStatus, values_callable=lambda x: [e.value for e in x]),
        nullable=False
    )
    source = db.Column(db.String(20), nullable=True, default='photo')

    __table_args__ = (
        db.UniqueConstraint('subject_id', 'attendance_date', 'student_roll_number',
                            name='_subject_date_roll_uc'),
    )


# ── AnnotatedPhoto Model ────────────────────────────────────────────────────
class AnnotatedPhoto(db.Model):
    __tablename__ = 'annotated_photos'
    id = db.Column(db.Integer, primary_key=True)
    subject_id = db.Column(db.Integer, db.ForeignKey('subjects.id', ondelete='CASCADE'), nullable=False)
    attendance_date = db.Column(db.Date, nullable=False)
    image_path = db.Column(db.String(255), nullable=False)
    teacher_id = db.Column(db.Integer, db.ForeignKey('teachers.id', ondelete='SET NULL'), nullable=True)

    __table_args__ = (
        db.UniqueConstraint('subject_id', 'attendance_date', 'image_path',
                            name='_subject_date_image_uc'),
    )


# ── Canonical Attendance Photo Model ────────────────────────────────────────
class Photo(db.Model):
    __tablename__ = 'photos'
    id = db.Column(db.Integer, primary_key=True)
    subject_id = db.Column(db.Integer, db.ForeignKey('subjects.id', ondelete='CASCADE'), nullable=False)
    academic_year = db.Column(db.String(20), nullable=False)
    semester = db.Column(db.String(20), nullable=False)
    attendance_date = db.Column(db.Date, nullable=False)
    session_type = db.Column(db.String(20), nullable=False, default='class')
    captured_photo_name = db.Column(db.String(255), nullable=False)
    raw_image_path = db.Column(db.String(255), nullable=False)
    annotated_image_path = db.Column(db.String(255), nullable=True)
    image_width = db.Column(db.Integer, nullable=False)
    image_height = db.Column(db.Integer, nullable=False)
    teacher_id = db.Column(db.Integer, db.ForeignKey('teachers.id', ondelete='SET NULL'), nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), server_default=db.func.now(), nullable=False)

    __table_args__ = (
        UniqueConstraint('subject_id', 'attendance_date', 'captured_photo_name',
                         name='_subject_date_photo_name_uc'),
    )

    bounding_boxes = db.relationship(
        'BoundingBox', backref='photo', lazy=True, cascade="all, delete-orphan"
    )


# ── Bounding Box Review / Audit Model ───────────────────────────────────────
class BoundingBox(db.Model):
    __tablename__ = 'bounding_boxes'
    id = db.Column(db.Integer, primary_key=True)
    photo_id = db.Column(db.Integer, db.ForeignKey('photos.id', ondelete='CASCADE'), nullable=False)
    student_roll_number = db.Column(
        db.String(80),
        db.ForeignKey('students.roll_number', ondelete='CASCADE'),
        nullable=False
    )
    bounding_box = db.Column(JSONB, nullable=False)
    identification_type = db.Column(db.String(50), nullable=False)
    is_approved = db.Column(db.Boolean, default=False, nullable=False)
    review_status = db.Column(db.String(20), default='pending', nullable=False)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    created_by_student_roll_number = db.Column(
        db.String(80),
        db.ForeignKey('students.roll_number', ondelete='SET NULL'),
        nullable=True
    )
    reviewed_by_teacher_id = db.Column(
        db.Integer,
        db.ForeignKey('teachers.id', ondelete='SET NULL'),
        nullable=True
    )
    request_group_id = db.Column(db.String(64), nullable=True)
    source_box_id = db.Column(
        db.Integer,
        db.ForeignKey('bounding_boxes.id', ondelete='SET NULL'),
        nullable=True
    )
    reviewed_at = db.Column(db.DateTime(timezone=True), nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), server_default=db.func.now(), nullable=False)

    source_box = db.relationship('BoundingBox', remote_side=[id], backref='child_requests', uselist=False)
