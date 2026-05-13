# ClassCam Platform Documentation (for AI Support Agent)

This document is a support knowledge base for answering user questions about the ClassCam Attendance System.

## 1. Product Summary

ClassCam is a role-based attendance platform with face-recognition support and post-capture review controls.

Primary portals:
- Student portal: `/student-portal`
- Staff portal (Admin / Professor / TA): `/teacher`
- Landing page: `/`

Primary attendance modes:
- Photo-based attendance (face recognition + bounding boxes)
- Manual CSV attendance upload

## 2. Roles and Permissions

### Student
Can:
- Register and verify OTP
- Enroll face data (required for full usage)
- Login and view subject attendance
- Enroll/unenroll in available subjects
- View attendance photos and request bounding-box corrections for self only

Cannot:
- Mark attendance for others
- Review/approve bounding-box requests

### TA
Can:
- Login after Admin approval
- Mark attendance for approved subjects
- View/download attendance reports for approved subjects
- Review pending student bounding-box requests for subjects where TA is approved

Cannot:
- Create/archive subjects
- Change professor
- Perform admin account management

### Professor
Can:
- Everything TA can for their managed subjects
- Create/archive/unarchive subjects
- Add TA requests and approve/remove TA assignments for managed subjects
- Unenroll students from managed subjects

### Admin
Can:
- All staff actions across subjects
- Approve/deny teacher registrations
- Remove teachers/students
- Change subject professor

## 3. Student User Flows

### 3.1 Student Sign Up
Path: Student portal -> `Register` tab.

Required fields:
- Roll Number
- Full Name
- IITJ email (`@iitj.ac.in` only)
- Password (minimum 6 chars)

Behavior:
- OTP is emailed.
- Response confirms code sent.

### 3.2 OTP Verification
Path after registration: `Verify Your Identity` view.

Rules:
- OTP is 6 digits.
- Expiry is 10 minutes.
- `Resend OTP` is available by context.

Successful verify:
- Student session starts.
- User proceeds to face enrollment.

### 3.3 Face Enrollment (Required)
Path: `Enroll Your Face` view, or profile button `Enroll Face Data Now` / `Update My Profile / Re-record Face`.

Input options:
- Camera recording (short video)
- File upload (image/video)

Validation:
- Image upload: exactly 1 face required.
- Video upload: multiple good frames extracted; minimum samples required.
- Unsupported file types are rejected.

On success:
- `is_verified = true`
- Enrollment asset is saved.
- Student profile photo is derived from enrollment data.

Important for support:
- There is no separate student “upload profile photo” endpoint.
- Student avatar shown in UI comes from enrollment media (`enrollment_data_path` / generated thumbnail).

### 3.4 Student Login
Path: Student portal -> `Login` tab.

Checks:
- Roll and password required
- Account must exist
- Password must be set
- Student must complete verification + face enrollment

### 3.5 Student Password Reset
Path: `Forgot Password?`

Flow:
1. Enter roll number to receive reset OTP.
2. Enter OTP + new password (min 6).

Rules:
- OTP expires in 10 minutes.

### 3.6 Subject Enrollment
In profile dashboard:
- `Available Subjects to Enroll` appears after face verification.
- Students can enroll or unenroll through portal actions.

### 3.7 View Attendance and Photos
Subject card click opens detail view with:
- Present/Absent/Total/% stats
- Attendance history table with source label (`Photo` or `Manual`)
- Attendance photo browser by date

### 3.8 Request Bounding-Box Corrections (Student)
Path: Subject detail -> Attendance Photos section.

Student can:
- Draw new boxes for self
- Mark existing approved self-boxes for deletion
- Submit grouped change set (`Submit Changes`)

Rules:
- Box min size is enforced (>= 12 px each side).
- Only own boxes can be changed.
- One pending request per photo per student at a time.
- While pending, editing for that photo is locked.

User-facing behavior:
- Pending request summary appears on photo card.
- Student sees review metadata once processed.

## 4. Staff User Flows

### 4.1 Staff Sign Up
Path: Staff portal -> `Register` tab.

Required:
- Username
- IITJ email (`@iitj.ac.in`)
- Password (min 6)
- Role (`Professor` or `TA`)

Behavior:
- Account is created as unapproved.
- Admin must approve before login succeeds.

### 4.2 Staff Login
Path: Staff portal -> `Login` tab.

If not approved:
- UI shows account awaiting approval.

### 4.3 Staff Password Reset
Path: Staff portal -> `Forgot Password`.

Flow:
1. Enter staff email.
2. Enter OTP + new password.

Rules:
- OTP expires in 10 minutes.

### 4.4 Teacher Profile Photo
Path: Top-right avatar in staff portal.

Options:
- Open webcam + capture
- Upload JPG/PNG

API:
- `POST /upload-profile-photo`

Validation:
- Allowed extensions: `.jpg`, `.jpeg`, `.png`

### 4.5 Mark Attendance (Staff)
Path: Staff dashboard -> `Mark Attendance` section.

Required fields:
- Subject
- Attendance date
- Optional session type (`class`, `lecture`, `tutorial`, `practical`)

Input modes (mutually exclusive in one submission):
- Photos (camera capture/upload)
- CSV upload

Optional:
- Send attendance email notifications to students

### CSV mode
Expected headers (flexible):
- roll identifier: `student_roll_number` or `roll_number`
- status: `status` / `attendance_status` / `attendance`

Behavior:
- Replaces attendance records for that subject/date.
- Source is marked `manual`.

### Photo mode
Behavior:
- Raw photo saved
- Face recognition runs
- Bounding boxes saved
- Annotated image generated
- Attendance recalculated from approved active boxes
- Source is `photo`

### 4.6 Bounding-Box Review Queue (Staff)
Path: Staff dashboard -> Pending bounding box review area.

Endpoints:
- Queue: `GET /bounding-box-review-queue`
- Action: `POST /review-bounding-box/<request_group_id>` with `decision=approve|reject`

Review abilities:
- See grouped add/delete requests per student/photo
- Compare proposed state on raw photo
- Review student assets
- Optionally edit proposed add-box coordinates before approval

Approval effects:
- Added boxes become approved + active
- Delete requests deactivate source boxes
- Annotation is re-rendered
- Attendance is recalculated for that subject/date

Reject effects:
- Pending request boxes are marked rejected/inactive
- Existing attendance state remains unchanged

### 4.7 Reports and Manual Edits
Staff can:
- Open report view: `/report?subject_id=<id>`
- Update individual statuses (`present`, `absent`, `-`) in report UI
- Download XLSX report: `/download-report?subject_id=<id>`

## 5. Subject and Staff Administration

Professor/Admin subject operations:
- Create subject
- Archive/unarchive subject
- Add TA request
- Approve/remove TA
- Unenroll student from subject

Admin-only operations:
- Approve/deny teacher registration
- Remove student/teacher
- Change professor for a subject
- Guardrails prevent deleting the last approved Admin or leaving a subject without any professor

## 6. Validation and Business Rules

General:
- Password minimum length: 6
- OTP length: 6 digits
- OTP validity: 10 minutes
- IITJ email required for registration/reset flows where applicable

Bounding box:
- Coordinates are sanitized/clamped to image dimensions
- Minimum box size: 12 x 12 pixels
- Student requests are grouped by `request_group_id`

Attendance source semantics:
- `photo`: derived from approved boxes in photos
- `manual`: uploaded CSV or manual status edits

## 7. Common User Issues and Support Answers

### 7.1 “Unexpected token <” during submit
Cause:
- Frontend expected JSON, but server returned HTML (historically on auth/CSRF failures).

Current behavior:
- API-style failures return JSON for bounding-box/review flows.
- Student UI shows friendly messages.

Support response:
- Ask user to refresh page and retry.
- If still failing, ask them to log out and log in again.

### 7.2 “Security token expired. Refresh the page and try again.”
Meaning:
- CSRF token missing/expired.

Action:
1. Refresh the page.
2. Retry action.
3. If needed, log out and log in again.

### 7.3 “This photo already has a pending review request.”
Meaning:
- Student already submitted one change set for that photo.

Action:
- Wait for TA/Professor/Admin review before new submission.

### 7.4 Face enrollment fails
Typical causes:
- Multiple faces in image
- Poor lighting/blur
- Unsupported file type
- Too few usable frames from video

Action:
- Use clear face-only input with better lighting and steady framing.

### 7.5 Camera not opening
Typical causes:
- Browser permission denied
- Insecure origin
- Camera busy in another app

Action:
- Use HTTPS deployment.
- Grant camera permission.
- Close other apps using camera.

## 8. Data Storage Notes

Static upload locations:
- Enrollment/profile assets: `static/enrollment_uploads`
- Raw attendance photos: `static/attendance_raw_uploads`
- Annotated attendance photos: `static/annotated_uploads`

Database highlights:
- Face encodings and bounding-box geometry use JSONB.
- Bounding-box audit fields preserve review history (`review_status`, reviewer, timestamps, source links).

## 9. Key Endpoints (Quick Reference)

Student:
- `POST /student-register`
- `POST /student-login`
- `POST /verify-otp`
- `POST /resend-otp`
- `POST /request-password-reset-otp`
- `POST /reset-password-with-otp`
- `POST /request-update-otp`
- `POST /verify-update-otp`
- `POST /enroll`
- `POST /enroll-in-subject`
- `POST /unenroll-from-subject`
- `GET /get-student-attendance-data?subject_id=...`
- `GET /get-student-photo-dates/<subject_id>`
- `GET /get-student-photos/<subject_id>/<YYYY-MM-DD>`
- `POST /student-bounding-box/submit`

Staff/Core:
- `POST /login`
- `POST /teacher-register`
- `POST /teacher-request-password-reset`
- `POST /teacher-reset-password`
- `POST /upload-profile-photo`
- `POST /process-attendance`
- `GET /bounding-box-review-queue`
- `POST /review-bounding-box/<request_group_id>`
- `GET /report?subject_id=...`
- `POST /update-attendance-status`
- `GET /download-report?subject_id=...`

Shared:
- `POST /change-password`
- `GET /logout`

## 10. AI Agent Response Guidelines

When answering users, always identify:
- Role (Student / TA / Professor / Admin)
- Portal screen they are on
- Exact error text shown
- Whether issue happened on login, attendance submission, or review action

For procedural questions, reply with:
1. Exact button path in UI.
2. Required prerequisites (e.g., face enrollment, approved account, subject access).
3. Expected success message/state.
4. Quick troubleshooting if blocked.

For data discrepancy issues (attendance mismatch), check in this order:
1. Was attendance source `manual` or `photo`?
2. Are there pending bounding-box requests not yet approved?
3. Was report manually edited after photo processing?
4. Was report manually edited after photo processing?
