"""initial schema

Revision ID: 20260902_0001
Revises:
Create Date: 2026-09-02 00:00:00
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = '20260902_0001'
down_revision = None
branch_labels = None
depends_on = None


attendance_status = sa.Enum(
    'present', 'absent', 'medical_leave', 'other_leave', name='attendancestatus'
)


def upgrade():
    op.create_table(
        'teachers',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('username', sa.String(length=80), nullable=False),
        sa.Column('email', sa.String(length=120), nullable=False),
        sa.Column('password_hash', sa.String(length=256), nullable=False),
        sa.Column('otp', sa.String(length=6), nullable=True),
        sa.Column('otp_generated_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('role', sa.String(length=50), nullable=False),
        sa.Column('is_approved', sa.Boolean(), nullable=False),
        sa.Column('profile_photo_path', sa.String(length=255), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('email'),
        sa.UniqueConstraint('username'),
    )
    op.create_table(
        'students',
        sa.Column('roll_number', sa.String(length=80), nullable=False),
        sa.Column('name', sa.String(length=120), nullable=False),
        sa.Column('email', sa.String(length=120), nullable=False),
        sa.Column('password_hash', sa.String(length=256), nullable=True),
        sa.Column('face_encodings', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('is_verified', sa.Boolean(), nullable=False),
        sa.Column('enrollment_data_path', sa.String(length=255), nullable=True),
        sa.Column('profile_photo_path', sa.String(length=255), nullable=True),
        sa.Column('otp', sa.String(length=6), nullable=True),
        sa.Column('otp_generated_at', sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint('roll_number'),
        sa.UniqueConstraint('email'),
    )
    op.create_table(
        'subjects',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('code', sa.String(length=50), nullable=False),
        sa.Column('name', sa.String(length=150), nullable=False),
        sa.Column('academic_year', sa.String(length=20), nullable=False),
        sa.Column('semester', sa.String(length=20), nullable=False),
        sa.Column('default_session_type', sa.String(length=20), nullable=False),
        sa.Column('archived', sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('code', 'academic_year', 'semester', name='_subject_term_uc'),
    )
    op.create_table(
        'enrollments',
        sa.Column('student_roll_number', sa.String(length=80), nullable=False),
        sa.Column('subject_id', sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(['student_roll_number'], ['students.roll_number'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['subject_id'], ['subjects.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('student_roll_number', 'subject_id'),
    )
    op.create_table(
        'subject_staff',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('teacher_id', sa.Integer(), nullable=False),
        sa.Column('subject_id', sa.Integer(), nullable=False),
        sa.Column('role_in_subject', sa.String(length=50), nullable=False),
        sa.Column('is_approved_by_prof', sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(['subject_id'], ['subjects.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['teacher_id'], ['teachers.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('teacher_id', 'subject_id', name='_teacher_subject_uc'),
    )
    op.create_table(
        'attendance_records',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('subject_id', sa.Integer(), nullable=False),
        sa.Column('student_roll_number', sa.String(length=80), nullable=False),
        sa.Column('attendance_date', sa.Date(), nullable=False),
        sa.Column('status', attendance_status, nullable=False),
        sa.Column('source', sa.String(length=20), nullable=True),
        sa.ForeignKeyConstraint(['student_roll_number'], ['students.roll_number'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['subject_id'], ['subjects.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('subject_id', 'attendance_date', 'student_roll_number', name='_subject_date_roll_uc'),
    )
    op.create_table(
        'annotated_photos',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('subject_id', sa.Integer(), nullable=False),
        sa.Column('attendance_date', sa.Date(), nullable=False),
        sa.Column('image_path', sa.String(length=255), nullable=False),
        sa.Column('teacher_id', sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(['subject_id'], ['subjects.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['teacher_id'], ['teachers.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('subject_id', 'attendance_date', 'image_path', name='_subject_date_image_uc'),
    )
    op.create_table(
        'photos',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('subject_id', sa.Integer(), nullable=False),
        sa.Column('academic_year', sa.String(length=20), nullable=False),
        sa.Column('semester', sa.String(length=20), nullable=False),
        sa.Column('attendance_date', sa.Date(), nullable=False),
        sa.Column('session_type', sa.String(length=20), nullable=False),
        sa.Column('captured_photo_name', sa.String(length=255), nullable=False),
        sa.Column('raw_image_path', sa.String(length=255), nullable=False),
        sa.Column('annotated_image_path', sa.String(length=255), nullable=True),
        sa.Column('image_width', sa.Integer(), nullable=False),
        sa.Column('image_height', sa.Integer(), nullable=False),
        sa.Column('teacher_id', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['subject_id'], ['subjects.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['teacher_id'], ['teachers.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('subject_id', 'attendance_date', 'captured_photo_name', name='_subject_date_photo_name_uc'),
    )
    op.create_table(
        'bounding_boxes',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('photo_id', sa.Integer(), nullable=False),
        sa.Column('student_roll_number', sa.String(length=80), nullable=False),
        sa.Column('bounding_box', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('identification_type', sa.String(length=50), nullable=False),
        sa.Column('is_approved', sa.Boolean(), nullable=False),
        sa.Column('review_status', sa.String(length=20), nullable=False),
        sa.Column('is_active', sa.Boolean(), nullable=False),
        sa.Column('created_by_student_roll_number', sa.String(length=80), nullable=True),
        sa.Column('reviewed_by_teacher_id', sa.Integer(), nullable=True),
        sa.Column('request_group_id', sa.String(length=64), nullable=True),
        sa.Column('source_box_id', sa.Integer(), nullable=True),
        sa.Column('reviewed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['created_by_student_roll_number'], ['students.roll_number'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['photo_id'], ['photos.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['reviewed_by_teacher_id'], ['teachers.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['source_box_id'], ['bounding_boxes.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['student_roll_number'], ['students.roll_number'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )


def downgrade():
    op.drop_table('bounding_boxes')
    op.drop_table('photos')
    op.drop_table('annotated_photos')
    op.drop_table('attendance_records')
    op.drop_table('subject_staff')
    op.drop_table('enrollments')
    op.drop_table('subjects')
    op.drop_table('students')
    op.drop_table('teachers')
    attendance_status.drop(op.get_bind(), checkfirst=True)
