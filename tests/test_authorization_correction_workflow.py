"""Integration coverage for correction requests and authorization boundaries.

These tests intentionally use PostgreSQL, matching the application's production
schema (including PostgreSQL JSON/enum columns).  Set TEST_DATABASE_URL to a
dedicated test database to run them; the suite is skipped otherwise.
"""

import os
from datetime import date

import pytest

from app import create_app
from app.extensions import db
from app.models import BoundingBox, Photo, Student, Subject, SubjectStaff, Teacher
from app.utils.attendance_review import (
    IDENTIFICATION_TYPE_ML,
    IDENTIFICATION_TYPE_USER_ADDED,
    REVIEW_STATUS_APPROVED,
    REVIEW_STATUS_PENDING,
)


TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")


class IntegrationConfig:
    TESTING = True
    SQLALCHEMY_DATABASE_URI = TEST_DATABASE_URL
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    WTF_CSRF_ENABLED = False
    RATELIMIT_ENABLED = False
    SECRET_KEY = "integration-test-secret-key"
    APP_ENV = "test"


@pytest.fixture(scope="module")
def app():
    if not TEST_DATABASE_URL:
        pytest.skip("Set TEST_DATABASE_URL to a dedicated PostgreSQL database")

    application = create_app(IntegrationConfig)
    with application.app_context():
        db.drop_all()
        db.create_all()
        yield application
        db.session.remove()
        db.drop_all()


@pytest.fixture()
def seeded(app):
    """Create an isolated subject, two teachers, two students, and one photo."""
    with app.app_context():
        db.session.remove()
        db.drop_all()
        db.create_all()
        professor = Teacher(username="professor", email="professor@iitj.ac.in", role="Professor", is_approved=True)
        outsider = Teacher(username="outsider", email="outsider@iitj.ac.in", role="Professor", is_approved=True)
        professor.set_password("password123")
        outsider.set_password("password123")

        student = Student(roll_number="S001", name="Student One", email="s001@example.com", is_verified=True)
        other_student = Student(roll_number="S002", name="Student Two", email="s002@example.com", is_verified=True)
        student.set_password("password123")
        other_student.set_password("password123")

        subject = Subject(code="CS101", name="Testing", academic_year="2026", semester="1")
        subject.students.extend([student, other_student])
        db.session.add_all([professor, outsider, student, other_student, subject])
        db.session.flush()
        db.session.add(SubjectStaff(
            teacher_id=professor.id,
            subject_id=subject.id,
            role_in_subject="Professor",
            is_approved_by_prof=True,
        ))
        photo = Photo(
            subject_id=subject.id,
            academic_year="2026",
            semester="1",
            attendance_date=date(2026, 9, 1),
            session_type="class",
            captured_photo_name="class.jpg",
            raw_image_path="/missing/class.jpg",
            image_width=640,
            image_height=480,
            teacher_id=professor.id,
        )
        db.session.add(photo)
        db.session.flush()
        original_box = BoundingBox(
            photo_id=photo.id,
            student_roll_number=student.roll_number,
            bounding_box={"top": 10, "right": 100, "bottom": 110, "left": 10},
            identification_type=IDENTIFICATION_TYPE_ML,
            is_approved=True,
            review_status=REVIEW_STATUS_APPROVED,
            is_active=True,
        )
        db.session.add(original_box)
        db.session.commit()
        data = {
            "professor": professor,
            "outsider": outsider,
            "student": student,
            "other_student": other_student,
            "subject": subject,
            "photo": photo,
            "original_box": original_box,
        }
        yield data
        db.session.remove()
        db.drop_all()


def login_student(client, student):
    response = client.post("/student-login", data={"roll_number": student.roll_number, "password": "password123"})
    assert response.status_code == 200


def login_teacher(client, teacher):
    response = client.post("/login", data={"username": teacher.username, "password": "password123"})
    assert response.status_code in (302, 303)


def test_anonymous_cannot_read_review_queue(app):
    response = app.test_client().get("/bounding-box-review-queue", headers={"Accept": "application/json"})
    assert response.status_code == 401
    assert response.get_json()["error"] == "Login required to access this resource."


def test_student_request_is_persisted_and_authorized_professor_can_approve(app, seeded):
    student_client = app.test_client()
    login_student(student_client, seeded["student"])
    submit = student_client.post(
        "/student-bounding-box/add",
        data={"photo_id": seeded["photo"].id, "top": "120", "right": "220", "bottom": "240", "left": "120"},
    )
    assert submit.status_code == 200
    group_id = submit.get_json()["request_group_id"]

    with app.app_context():
        pending = BoundingBox.query.filter_by(request_group_id=group_id).one()
        assert pending.review_status == REVIEW_STATUS_PENDING
        assert pending.identification_type == IDENTIFICATION_TYPE_USER_ADDED

    professor_client = app.test_client()
    login_teacher(professor_client, seeded["professor"])
    queue = professor_client.get("/bounding-box-review-queue")
    assert queue.status_code == 200
    assert any(item["request_group_id"] == group_id for item in queue.get_json())

    approved = professor_client.post(
        f"/review-bounding-box/{group_id}",
        data={"decision": "approve", "add_boxes": '[{"top": 125, "right": 225, "bottom": 245, "left": 125}]'},
    )
    assert approved.status_code == 200

    with app.app_context():
        pending = BoundingBox.query.filter_by(request_group_id=group_id).one()
        assert pending.review_status == REVIEW_STATUS_APPROVED
        assert pending.is_active is True
        assert pending.reviewed_by_teacher_id == seeded["professor"].id


def test_teacher_without_subject_access_cannot_approve_request(app, seeded):
    student_client = app.test_client()
    login_student(student_client, seeded["student"])
    submit = student_client.post(
        "/student-bounding-box/add",
        data={"photo_id": seeded["photo"].id, "top": "130", "right": "230", "bottom": "250", "left": "130"},
    )
    group_id = submit.get_json()["request_group_id"]

    outsider_client = app.test_client()
    login_teacher(outsider_client, seeded["outsider"])
    response = outsider_client.post(f"/review-bounding-box/{group_id}", data={"decision": "reject"})
    assert response.status_code == 403
    assert response.get_json()["error"] == "Unauthorized for this subject."

    with app.app_context():
        pending = BoundingBox.query.filter_by(request_group_id=group_id).one()
        assert pending.review_status == REVIEW_STATUS_PENDING


def test_student_cannot_submit_correction_for_unenrolled_subject(app, seeded):
    student_client = app.test_client()
    login_student(student_client, seeded["other_student"])
    # Remove the second student's enrollment while preserving the shared fixture photo.
    with app.app_context():
        seeded["subject"].students.remove(seeded["other_student"])
        db.session.commit()

    response = student_client.post(
        "/student-bounding-box/add",
        data={"photo_id": seeded["photo"].id, "top": "130", "right": "230", "bottom": "250", "left": "130"},
    )
    assert response.status_code == 403
    assert response.get_json()["error"] == "Not enrolled in this subject."
