"""
utils/attendance_review.py — Attendance Photo / Bounding Box Helpers
====================================================================
Shared workflow for:
  - storing canonical photo metadata
  - rendering approved annotations
  - recalculating attendance from approved active boxes
  - validating student-requested bounding boxes
"""

import os
from collections import OrderedDict
from datetime import timezone
from zoneinfo import ZoneInfo

import cv2
from flask import current_app, has_request_context, url_for

from app.extensions import db
from app.models import AnnotatedPhoto, AttendanceRecord, BoundingBox, Photo, Student, Subject, AttendanceStatus


IDENTIFICATION_TYPE_ML = 'ML detected'
IDENTIFICATION_TYPE_USER_CORRECTED = 'User corrected'
IDENTIFICATION_TYPE_USER_ADDED = 'User added'
IDENTIFICATION_TYPE_USER_DELETED = 'User deleted'

REVIEW_STATUS_PENDING = 'pending'
REVIEW_STATUS_APPROVED = 'approved'
REVIEW_STATUS_REJECTED = 'rejected'

VALID_SESSION_TYPES = {'class', 'lecture', 'tutorial', 'practical'}
ATTENDANCE_SOURCE_PHOTO = 'photo'
ATTENDANCE_SOURCE_MANUAL = 'manual'
VALID_ATTENDANCE_SOURCES = {ATTENDANCE_SOURCE_PHOTO, ATTENDANCE_SOURCE_MANUAL}
IST_TIMEZONE = ZoneInfo('Asia/Kolkata')


def annotation_dir():
    directory = os.path.join(current_app.static_folder, 'annotated_uploads')
    os.makedirs(directory, exist_ok=True)
    return directory


def raw_dir():
    directory = os.path.join(current_app.static_folder, 'attendance_raw_uploads')
    os.makedirs(directory, exist_ok=True)
    return directory


def normalize_session_type(value, default='class'):
    normalized = (value or default or 'class').strip().lower()
    return normalized if normalized in VALID_SESSION_TYPES else (default if default in VALID_SESSION_TYPES else 'class')


def normalize_attendance_source(value, default=ATTENDANCE_SOURCE_PHOTO):
    normalized = (value or default or ATTENDANCE_SOURCE_PHOTO).strip().lower()
    if not normalized:
        return default
    return normalized if normalized in VALID_ATTENDANCE_SOURCES else default


def attendance_source_label(value):
    normalized = normalize_attendance_source(value)
    if normalized == ATTENDANCE_SOURCE_MANUAL:
        return 'Manual'
    return 'Photo'


def photo_backed_attendance_dates(subject_id):
    canonical_dates = {
        row.attendance_date
        for row in db.session.query(Photo.attendance_date)
        .filter(Photo.subject_id == subject_id)
        .distinct()
        .all()
    }
    legacy_dates = {
        row.attendance_date
        for row in db.session.query(AnnotatedPhoto.attendance_date)
        .filter(AnnotatedPhoto.subject_id == subject_id)
        .distinct()
        .all()
    }
    return canonical_dates | legacy_dates


def infer_attendance_source(value, attendance_date=None, photo_backed_dates=None):
    normalized = (value or '').strip().lower()
    if normalized in VALID_ATTENDANCE_SOURCES:
        return normalized
    if attendance_date is not None and photo_backed_dates is not None and attendance_date in photo_backed_dates:
        return ATTENDANCE_SOURCE_PHOTO
    return ATTENDANCE_SOURCE_MANUAL


def _ensure_timezone(value):
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def format_datetime_ist(value):
    normalized = _ensure_timezone(value)
    if normalized is None:
        return None
    return normalized.astimezone(IST_TIMEZONE).strftime('%d %b %Y, %I:%M %p IST')


def teacher_display_name(teacher):
    if not teacher:
        return None
    if teacher.role:
        return f'{teacher.username} ({teacher.role})'
    return teacher.username


def bbox_from_face_location(top, right, bottom, left):
    return {
        'top': int(top),
        'right': int(right),
        'bottom': int(bottom),
        'left': int(left),
    }


def sanitize_bounding_box(raw_bbox, image_width, image_height, min_size=12):
    try:
        top = int(round(float(raw_bbox.get('top'))))
        right = int(round(float(raw_bbox.get('right'))))
        bottom = int(round(float(raw_bbox.get('bottom'))))
        left = int(round(float(raw_bbox.get('left'))))
    except (AttributeError, TypeError, ValueError):
        return None

    top = max(0, min(image_height - 1, top))
    bottom = max(0, min(image_height, bottom))
    left = max(0, min(image_width - 1, left))
    right = max(0, min(image_width, right))

    if bottom - top < min_size or right - left < min_size:
        return None

    return {
        'top': top,
        'right': right,
        'bottom': bottom,
        'left': left,
    }


def static_fs_path_from_web_path(web_path):
    if not web_path:
        return None

    static_marker = '/static/'
    if static_marker in web_path:
        rel_path = web_path.split(static_marker, 1)[1]
    else:
        rel_path = web_path.lstrip('/')
    return os.path.join(current_app.static_folder, rel_path)


def static_web_path(subdir, filename):
    if has_request_context():
        return url_for('static', filename=f'{subdir}/{filename}')
    return f'/static/{subdir}/{filename}'


def _delete_static_file(web_path):
    file_path = static_fs_path_from_web_path(web_path)
    if file_path and os.path.exists(file_path):
        try:
            os.remove(file_path)
        except OSError:
            pass


def _draw_box(img, bbox, label, color):
    top = bbox['top']
    right = bbox['right']
    bottom = bbox['bottom']
    left = bbox['left']

    cv2.rectangle(img, (left, top), (right, bottom), color, 2)
    label_bottom = max(bottom, 35)
    cv2.rectangle(img, (left, label_bottom - 35), (right, label_bottom), color, cv2.FILLED)
    cv2.putText(
        img,
        label,
        (left + 6, label_bottom - 8),
        cv2.FONT_HERSHEY_DUPLEX,
        0.55,
        (255, 255, 255),
        1
    )


def render_photo_annotation(photo):
    raw_path = static_fs_path_from_web_path(photo.raw_image_path)
    if not raw_path or not os.path.exists(raw_path):
        return None

    img = cv2.imread(raw_path)
    if img is None:
        return None

    approved_boxes = BoundingBox.query.filter_by(
        photo_id=photo.id,
        is_active=True,
        is_approved=True
    ).order_by(BoundingBox.id).all()

    for box in approved_boxes:
        label = box.student_roll_number
        _draw_box(img, box.bounding_box, label, (34, 197, 94))

    base_name, _ = os.path.splitext(photo.captured_photo_name)
    annotated_fname = f"{base_name}_photo{photo.id}_annotated.jpg"
    annotated_fs_path = os.path.join(annotation_dir(), annotated_fname)
    cv2.imwrite(annotated_fs_path, img)

    previous_path = photo.annotated_image_path
    photo.annotated_image_path = static_web_path('annotated_uploads', annotated_fname)
    if previous_path and previous_path != photo.annotated_image_path:
        _delete_static_file(previous_path)
    return photo.annotated_image_path


def replace_ml_boxes_for_photo(photo, detections):
    BoundingBox.query.filter_by(photo_id=photo.id).delete(synchronize_session=False)

    present_rolls = set()
    for detection in detections:
        roll = detection.get('roll_number')
        bbox = detection.get('bounding_box')
        if not roll or not bbox:
            continue

        present_rolls.add(roll)
        db.session.add(BoundingBox(
            photo_id=photo.id,
            student_roll_number=roll,
            bounding_box=bbox,
            identification_type=IDENTIFICATION_TYPE_ML,
            is_approved=True,
            review_status=REVIEW_STATUS_APPROVED,
            is_active=True
        ))

    db.session.flush()
    render_photo_annotation(photo)
    return present_rolls


def replace_attendance_records_for_subject_date(subject_id, attendance_date, attendance_status_by_roll, source=ATTENDANCE_SOURCE_PHOTO):
    normalized_source = normalize_attendance_source(source)

    AttendanceRecord.query.filter_by(
        subject_id=subject_id,
        attendance_date=attendance_date
    ).delete(synchronize_session=False)

    new_records = []
    for roll, status_val in sorted(attendance_status_by_roll.items()):
        try:
            # Convert string to Enum member
            status_enum = AttendanceStatus(status_val)
        except ValueError:
            continue
        new_records.append(AttendanceRecord(
            subject_id=subject_id,
            student_roll_number=roll,
            attendance_date=attendance_date,
            status=status_enum,
            source=normalized_source
        ))
    if new_records:
        db.session.add_all(new_records)

    present_rolls = sorted([roll for roll, status in attendance_status_by_roll.items() if status == AttendanceStatus.PRESENT.value or status == AttendanceStatus.PRESENT])
    absent_rolls = sorted([roll for roll, status in attendance_status_by_roll.items() if status == AttendanceStatus.ABSENT.value or status == AttendanceStatus.ABSENT])
    medical_rolls = sorted([roll for roll, status in attendance_status_by_roll.items() if status == AttendanceStatus.MEDICAL_LEAVE.value or status == AttendanceStatus.MEDICAL_LEAVE])
    other_rolls = sorted([roll for roll, status in attendance_status_by_roll.items() if status == AttendanceStatus.OTHER_LEAVE.value or status == AttendanceStatus.OTHER_LEAVE])
    return {
        'present_count': len(present_rolls),
        'absent_count': len(absent_rolls),
        'medical_leave_count': len(medical_rolls),
        'other_leave_count': len(other_rolls),
        'present_rolls': present_rolls,
        'absent_rolls': absent_rolls,
        'medical_leave_rolls': medical_rolls,
        'other_leave_rolls': other_rolls
    }


def recalculate_attendance_for_subject_date(subject_id, attendance_date):
    subject = db.session.get(Subject, subject_id)
    if not subject:
        return {'present_rolls': [], 'absent_rolls': []}

    enrolled_students = subject.students.filter(
        Student.is_verified == True,
        Student.face_encodings.isnot(None)
    ).all()
    all_rolls = {student.roll_number for student in enrolled_students}

    approved_boxes = BoundingBox.query.join(Photo).filter(
        Photo.subject_id == subject_id,
        Photo.attendance_date == attendance_date,
        BoundingBox.is_active == True,
        BoundingBox.is_approved == True
    ).all()
    present_rolls = {box.student_roll_number for box in approved_boxes if box.student_roll_number}

    # Fetch existing manual leave statuses to preserve them
    existing_leaves = {
        r.student_roll_number: r.status 
        for r in AttendanceRecord.query.filter(
            AttendanceRecord.subject_id == subject_id,
            AttendanceRecord.attendance_date == attendance_date,
            AttendanceRecord.status.in_([AttendanceStatus.MEDICAL_LEAVE, AttendanceStatus.OTHER_LEAVE])
        ).all()
    }

    attendance_status_by_roll = {}
    for roll in sorted(all_rolls):
        if roll in present_rolls:
            attendance_status_by_roll[roll] = AttendanceStatus.PRESENT.value
        elif roll in existing_leaves:
            # preserve the enum value string
            attendance_status_by_roll[roll] = existing_leaves[roll].value
        else:
            attendance_status_by_roll[roll] = AttendanceStatus.ABSENT.value

    return replace_attendance_records_for_subject_date(
        subject_id,
        attendance_date,
        attendance_status_by_roll,
        source=ATTENDANCE_SOURCE_PHOTO
    )


def build_box_payload(box):
    return {
        'id': box.id,
        'student_roll_number': box.student_roll_number,
        'bounding_box': box.bounding_box,
        'identification_type': box.identification_type,
        'is_approved': box.is_approved,
        'review_status': box.review_status,
        'is_active': box.is_active,
        'request_group_id': box.request_group_id,
        'source_box_id': box.source_box_id,
        'created_at_ist': format_datetime_ist(box.created_at),
        'reviewed_at_ist': format_datetime_ist(box.reviewed_at),
        'reviewed_by': teacher_display_name(box.reviewed_by),
        'reviewed_by_profile_photo_url': box.reviewed_by.profile_photo_url if box.reviewed_by else None,
    }


def request_group_key(box):
    return box.request_group_id or f'legacy-{box.id}'


def group_boxes_by_request_group(boxes):
    grouped = OrderedDict()
    for box in boxes:
        grouped.setdefault(request_group_key(box), []).append(box)
    return grouped


def split_request_boxes(request_boxes):
    add_boxes = []
    delete_boxes = []
    for box in request_boxes:
        if box.identification_type == IDENTIFICATION_TYPE_USER_ADDED:
            add_boxes.append(box)
        elif box.identification_type == IDENTIFICATION_TYPE_USER_DELETED:
            delete_boxes.append(box)
    return add_boxes, delete_boxes


def build_request_proposal(approved_boxes, request_boxes):
    add_boxes, delete_boxes = split_request_boxes(request_boxes)
    removed_source_ids = {box.source_box_id for box in delete_boxes if box.source_box_id}
    proposed_boxes = [box for box in approved_boxes if box.id not in removed_source_ids]
    proposed_boxes.extend(add_boxes)
    return {
        'add_boxes': add_boxes,
        'delete_boxes': delete_boxes,
        'removed_source_ids': removed_source_ids,
        'proposed_boxes': proposed_boxes,
    }


def build_review_event_payload(request_boxes):
    if not request_boxes:
        return None

    representative = request_boxes[0]
    student = representative.box_student or representative.requested_by_student
    reviewer = None
    reviewed_at = None
    for box in request_boxes:
        if reviewer is None and box.reviewed_by:
            reviewer = box.reviewed_by
        if reviewed_at is None and box.reviewed_at:
            reviewed_at = box.reviewed_at

    add_boxes, delete_boxes = split_request_boxes(request_boxes)
    return {
        'request_group_id': request_group_key(representative),
        'student_roll_number': representative.student_roll_number,
        'student_name': student.name if student else representative.student_roll_number,
        'posted_at_ist': format_datetime_ist(representative.created_at),
        'accepted_at_ist': format_datetime_ist(reviewed_at),
        'accepted_by': teacher_display_name(reviewer),
        'accepted_by_profile_photo_url': reviewer.profile_photo_url if reviewer else None,
        'add_count': len(add_boxes),
        'delete_count': len(delete_boxes),
    }


def registered_asset_kind(path):
    if not path:
        return None
    lowered = path.lower()
    if lowered.endswith(('.mp4', '.webm', '.mov', '.avi', '.mkv')):
        return 'video'
    return 'image'
