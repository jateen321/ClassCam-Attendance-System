"""
create_demo_data.py
===================
Local-only demo seed for the attendance report page.

Run from the repo root:
    python create_demo_data.py
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Iterable
from datetime import date

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy import text

from app import create_app
from app.extensions import db
from app.models import AttendanceRecord, AttendanceStatus, Student, Subject
from app.utils.attendance_review import ATTENDANCE_SOURCE_MANUAL, ATTENDANCE_SOURCE_PHOTO


DEMO_SUBJECT_CODE = "DEMO-FORMAT-101"
DEMO_ACADEMIC_YEAR = "2025-26"
DEMO_SEMESTER = "Spring"
DEMO_SUBJECT_NAME = "Demo Attendance Format"
DEMO_SESSION_TYPE = "class"

DEMO_DATES = [
    ("2026-03-17", ATTENDANCE_SOURCE_PHOTO),
    ("2026-03-18", ATTENDANCE_SOURCE_PHOTO),
    ("2026-03-19", ATTENDANCE_SOURCE_PHOTO),
    ("2026-03-20", ATTENDANCE_SOURCE_MANUAL),
    ("2026-03-23", ATTENDANCE_SOURCE_MANUAL),
    ("2026-03-24", ATTENDANCE_SOURCE_PHOTO),
    ("2026-03-25", ATTENDANCE_SOURCE_MANUAL),
]

FIRST_NAMES = [
    "Aarav",
    "Isha",
    "Rohan",
    "Meera",
    "Kabir",
    "Ananya",
    "Vihaan",
    "Saanvi",
    "Arjun",
    "Diya",
    "Reyansh",
    "Kavya",
]

LAST_NAMES = [
    "Mehta",
    "Sharma",
    "Kapoor",
    "Iyer",
    "Gupta",
    "Nair",
    "Bose",
    "Chopra",
    "Reddy",
    "Patel",
    "Verma",
    "Joshi",
]


def build_student_specs() -> list[dict[str, str]]:
    specs = []
    for index in range(12):
        roll_number = f"DEMO26CS{index + 1:03d}"
        name = f"{FIRST_NAMES[index % len(FIRST_NAMES)]} {LAST_NAMES[(index * 2) % len(LAST_NAMES)]}"
        email = f"demo26cs{index + 1:03d}@classcam.local"
        specs.append({
            "roll_number": roll_number,
            "name": name,
            "email": email,
        })
    return specs


def status_for_cell(student_index: int, date_index: int) -> AttendanceStatus:
    cycle = [
        AttendanceStatus.PRESENT,
        AttendanceStatus.PRESENT,
        AttendanceStatus.ABSENT,
        AttendanceStatus.PRESENT,
        AttendanceStatus.MEDICAL_LEAVE,
        AttendanceStatus.PRESENT,
        AttendanceStatus.OTHER_LEAVE,
        AttendanceStatus.PRESENT,
        AttendanceStatus.ABSENT,
        AttendanceStatus.PRESENT,
        AttendanceStatus.MEDICAL_LEAVE,
        AttendanceStatus.OTHER_LEAVE,
    ]
    return cycle[(student_index + date_index * 3) % len(cycle)]


def seed_enrollments(subject: Subject, students: Iterable[Student]) -> None:
    db.session.execute(
        text("DELETE FROM enrollments WHERE subject_id = :subject_id"),
        {"subject_id": subject.id},
    )
    for student in students:
        student.subjects.append(subject)


def main() -> None:
    app = create_app()

    with app.app_context():
        subject = Subject.query.filter_by(
            code=DEMO_SUBJECT_CODE,
            academic_year=DEMO_ACADEMIC_YEAR,
            semester=DEMO_SEMESTER,
        ).one_or_none()

        if subject is None:
            subject = Subject(
                code=DEMO_SUBJECT_CODE,
                name=DEMO_SUBJECT_NAME,
                academic_year=DEMO_ACADEMIC_YEAR,
                semester=DEMO_SEMESTER,
                default_session_type=DEMO_SESSION_TYPE,
                archived=False,
            )
            db.session.add(subject)
            db.session.flush()
        else:
            subject.name = DEMO_SUBJECT_NAME
            subject.academic_year = DEMO_ACADEMIC_YEAR
            subject.semester = DEMO_SEMESTER
            subject.default_session_type = DEMO_SESSION_TYPE
            subject.archived = False

        demo_students: list[Student] = []
        student_specs = build_student_specs()
        for spec in student_specs:
            student = Student.query.filter_by(roll_number=spec["roll_number"]).one_or_none()
            if student is None:
                student = Student(
                    roll_number=spec["roll_number"],
                    name=spec["name"],
                    email=spec["email"],
                    is_verified=True,
                )
                student.set_password("demo-password")
                db.session.add(student)
            else:
                student.name = spec["name"]
                student.email = spec["email"]
                student.is_verified = True
                if not student.password_hash:
                    student.set_password("demo-password")
            demo_students.append(student)

        db.session.flush()

        seed_enrollments(subject, demo_students)

        db.session.query(AttendanceRecord).filter_by(subject_id=subject.id).delete(synchronize_session=False)

        status_counts = {
            AttendanceStatus.PRESENT.value: 0,
            AttendanceStatus.ABSENT.value: 0,
            AttendanceStatus.MEDICAL_LEAVE.value: 0,
            AttendanceStatus.OTHER_LEAVE.value: 0,
        }
        source_counts = {ATTENDANCE_SOURCE_PHOTO: 0, ATTENDANCE_SOURCE_MANUAL: 0}

        for date_index, (date_str, source) in enumerate(DEMO_DATES):
            attendance_date = date.fromisoformat(date_str)
            source_counts[source] += len(demo_students)
            for student_index, student in enumerate(demo_students):
                status = status_for_cell(student_index, date_index)
                status_counts[status.value] += 1
                db.session.add(
                    AttendanceRecord(
                        subject_id=subject.id,
                        student_roll_number=student.roll_number,
                        attendance_date=attendance_date,
                        status=status,
                        source=source,
                    )
                )

        db.session.commit()

        total_records = len(demo_students) * len(DEMO_DATES)
        print(
            "Seeded demo subject "
            f"{subject.code} (id={subject.id}) with {len(demo_students)} students "
            f"and {total_records} attendance rows across {len(DEMO_DATES)} dates."
        )
        print(
            "Status counts: "
            f"present={status_counts[AttendanceStatus.PRESENT.value]}, "
            f"absent={status_counts[AttendanceStatus.ABSENT.value]}, "
            f"medical_leave={status_counts[AttendanceStatus.MEDICAL_LEAVE.value]}, "
            f"other_leave={status_counts[AttendanceStatus.OTHER_LEAVE.value]}"
        )
        print(
            "Source counts: "
            f"photo={source_counts[ATTENDANCE_SOURCE_PHOTO]}, "
            f"manual={source_counts[ATTENDANCE_SOURCE_MANUAL]}"
        )


if __name__ == "__main__":
    main()
