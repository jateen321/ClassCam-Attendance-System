from datetime import date, datetime, timezone
from types import SimpleNamespace

import pytest

from app.utils.attendance_review import (
    ATTENDANCE_SOURCE_MANUAL,
    ATTENDANCE_SOURCE_PHOTO,
    attendance_source_label,
    bbox_from_face_location,
    format_datetime_ist,
    infer_attendance_source,
    normalize_attendance_source,
    normalize_session_type,
    sanitize_bounding_box,
    teacher_display_name,
)


@pytest.mark.parametrize(
    ("raw_value", "default", "expected"),
    [
        (" Lecture ", "class", "lecture"),
        (None, "tutorial", "tutorial"),
        ("invalid", "practical", "practical"),
        ("invalid", "invalid-default", "class"),
    ],
)
def test_normalize_session_type(raw_value, default, expected):
    assert normalize_session_type(raw_value, default) == expected


@pytest.mark.parametrize(
    ("raw_value", "default", "expected"),
    [
        (" MANUAL ", ATTENDANCE_SOURCE_PHOTO, ATTENDANCE_SOURCE_MANUAL),
        (None, ATTENDANCE_SOURCE_MANUAL, ATTENDANCE_SOURCE_MANUAL),
        ("unknown", ATTENDANCE_SOURCE_PHOTO, ATTENDANCE_SOURCE_PHOTO),
    ],
)
def test_normalize_attendance_source(raw_value, default, expected):
    assert normalize_attendance_source(raw_value, default) == expected


def test_attendance_source_label_uses_normalized_source():
    assert attendance_source_label("manual") == "Manual"
    assert attendance_source_label("unexpected") == "Photo"


def test_infer_attendance_source_prefers_an_explicit_valid_source():
    attendance_date = date(2026, 8, 28)

    assert infer_attendance_source(
        "manual",
        attendance_date,
        {attendance_date},
    ) == ATTENDANCE_SOURCE_MANUAL


def test_infer_attendance_source_uses_photo_evidence_for_legacy_records():
    attendance_date = date(2026, 8, 28)

    assert infer_attendance_source(
        None,
        attendance_date,
        {attendance_date},
    ) == ATTENDANCE_SOURCE_PHOTO


def test_infer_attendance_source_defaults_legacy_record_to_manual():
    assert infer_attendance_source(None) == ATTENDANCE_SOURCE_MANUAL


def test_format_datetime_ist_converts_utc_time():
    utc_value = datetime(2026, 8, 28, 6, 30, tzinfo=timezone.utc)

    assert format_datetime_ist(utc_value) == "28 Aug 2026, 12:00 PM IST"


def test_format_datetime_ist_treats_naive_time_as_utc():
    naive_value = datetime(2026, 8, 28, 6, 30)

    assert format_datetime_ist(naive_value) == "28 Aug 2026, 12:00 PM IST"


def test_teacher_display_name_includes_role_when_available():
    teacher = SimpleNamespace(username="anita", role="Professor")

    assert teacher_display_name(teacher) == "anita (Professor)"
    assert teacher_display_name(None) is None


def test_bbox_from_face_location_returns_integer_coordinates():
    assert bbox_from_face_location(1.8, 50.2, 70.9, 3.1) == {
        "top": 1,
        "right": 50,
        "bottom": 70,
        "left": 3,
    }


def test_sanitize_bounding_box_rounds_and_clamps_coordinates():
    raw_box = {"top": "-4", "right": "120.4", "bottom": "80.6", "left": "5.2"}

    assert sanitize_bounding_box(raw_box, image_width=100, image_height=80) == {
        "top": 0,
        "right": 100,
        "bottom": 80,
        "left": 5,
    }


@pytest.mark.parametrize(
    "raw_box",
    [
        {"top": 10, "right": 20, "bottom": 15, "left": 10},
        {"top": "bad", "right": 50, "bottom": 60, "left": 10},
        None,
    ],
)
def test_sanitize_bounding_box_rejects_invalid_or_too_small_boxes(raw_box):
    assert sanitize_bounding_box(raw_box, image_width=100, image_height=80) is None
