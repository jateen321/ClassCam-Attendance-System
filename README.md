# ClassCam Attendance System

ClassCam is a production-oriented, role-based attendance platform built with Flask, PostgreSQL, and face recognition.

**Product of Clarity Lab.**

## Overview

ClassCam supports end-to-end attendance operations for educational settings:
- Student onboarding with OTP + face enrollment
- Staff-controlled attendance marking from photos or CSV
- Subject-level reporting and manual corrections
- Auditable bounding-box review workflow for attendance corrections

## Core Capabilities

- Role-based portals: Student, TA, Professor, Admin
- Face-based attendance with annotated photo evidence
- Manual CSV attendance support for offline/backup workflows
- Student correction requests (add/delete bounding boxes) with staff approval queue
- Attendance source tracking (`photo` vs `manual`)
- Subject and staffing lifecycle management (create/archive/unarchive, TA approvals, professor transfer)
- Password reset flows for students and staff via OTP
- Teacher profile photo management (upload or webcam capture)

## System Architecture

- `web` service: Flask app served by Gunicorn (`gunicorn -c gunicorn.conf.py wsgi:app`)
- `db` service: PostgreSQL 15
- `nginx` service: reverse proxy + TLS termination
- `adminer` service: pgAdmin (optional DB administration)

Persistent data:
- DB volume: `postgres_data`
- Static uploads (attendance/enrollment/annotations): `./static_data -> /app/static`

## Tech Stack

- Backend: Python, Flask, Flask-SQLAlchemy, Flask-Login, Flask-WTF
- WSGI server: Gunicorn (`gthread` workers)
- Database: PostgreSQL (JSONB for encodings and box geometry)
- Computer Vision: `face_recognition`, OpenCV
- Frontend: Jinja templates, JavaScript, Tailwind CSS
- Infra: Docker, Docker Compose, Nginx

## Quick Start (Docker)

### 1. Prerequisites

- Docker + Docker Compose
- OpenSSL (for local TLS certificate generation)

### 2. Configure Environment

Create `.env` in the repository root.

```env
# Flask
SECRET_KEY=change_this_to_a_strong_secret

# Database
DB_NAME=attendance_db
DB_USER=postgres
DB_PASS=postgres
DB_HOST=db
DB_PORT=5432

# Optional: seed/sync default admin at startup
DEFAULT_ADMIN_EMAIL=admin@iitj.ac.in
DEFAULT_ADMIN_USERNAME=admin
DEFAULT_ADMIN_PASSWORD=change_this_admin_password

# Email (OTP + notifications)
SENDER_EMAIL=your_email@iitj.ac.in
SENDER_PASSWORD=your_app_password
```

Notes:
- Use app passwords for email providers that require them.
- Do not commit real credentials.

### 3. Prepare Local Directories

```bash
mkdir -p static_data certs
```

### 4. Generate TLS Certificates (Local)

```bash
openssl req -x509 -newkey rsa:4096 -nodes \
  -out certs/cert.pem \
  -keyout certs/key.pem \
  -days 365 \
  -subj "/C=IN/ST=State/L=City/O=Clarity Lab/CN=localhost"
```

For LAN/mobile testing, replace `CN=localhost` with your machine IP.

### 5. Start Services

```bash
docker compose up -d --build
```

### 6. Access the App

- HTTPS (Nginx TLS): `https://localhost:11000`
- HTTP entrypoint (redirect configured): `http://localhost`
- pgAdmin: `http://localhost:8080`

Because certificates are self-signed in local setups, browser trust warnings are expected.

## Admin Bootstrapping

Preferred path:
- Set `DEFAULT_ADMIN_EMAIL`, `DEFAULT_ADMIN_USERNAME`, and `DEFAULT_ADMIN_PASSWORD` in `.env`.
- On app startup, default admin is created/synchronized automatically.

Fallback path:
```bash
docker compose exec web python create_teacher.py
```

## Common Workflows

### Student
- Register -> verify OTP -> enroll face -> login
- Enroll in subjects
- View attendance history/photos
- Submit bounding-box correction requests for own face detections

### Staff (TA/Professor/Admin)
- Login after approval
- Mark attendance via photos or CSV
- Review student correction queue
- View/edit attendance reports and export XLSX

## Operations

Useful commands:

```bash
# Rebuild and restart

docker compose up -d --build

# View logs

docker compose logs -f web

```

### Web Server Tuning

On startup the container runs `init_db.py` (schema + admin seed) once, then execs
Gunicorn. Defaults live in `gunicorn.conf.py` and are overridable in `.env`:

| Variable | Default | Notes |
| --- | --- | --- |
| `GUNICORN_WORKERS` | `max(2, CPUs / 2)` | Each worker owns a 2-thread face-recognition pool, so real CPU load is `workers x 2`. |
| `GUNICORN_THREADS` | `4` | Handlers block on face recognition and SMTP; threads keep the worker responsive. |
| `GUNICORN_TIMEOUT` | `120` | Raised from Gunicorn's 30s default — multi-photo batches exceed it. |
| `GUNICORN_LOG_LEVEL` | `info` | |
| `DB_WAIT_ATTEMPTS` | `10` | `init_db.py` retries with exponential backoff before giving up. |
| `DB_WAIT_DELAY` | `1.0` | Seconds before the first retry; doubles up to 15s. |

If the database never becomes reachable, `init_db.py` exits non-zero and the
entrypoint aborts — the container fails fast instead of serving against a
missing schema.

For local development without Docker, `python app.py` still runs the Flask dev
server (and initialises the DB first). Do not use it to serve real traffic.

## Troubleshooting

- Camera access denied:
  - Use HTTPS.
  - Allow camera permissions in browser.
- OTP email not delivered:
  - Verify `SENDER_EMAIL` / `SENDER_PASSWORD`.
  - Check provider app-password settings.
- Bounding-box submit errors:
  - Refresh page and retry (CSRF/session token refresh).
  - Re-login if session expired.
- DB connectivity issues:
  - Ensure `.env` database values match `db` service settings.

## Repository Structure

- `app/` - Flask app package (routes, models, utilities)
- `templates/` - Jinja templates for student/staff/report views
- `static/` - static assets served by Flask
- `nginx/` - Nginx configuration
- `docker-compose.yml` - multi-service local deployment

## Notes

This repository contains both product logic and operational scripts for local development/deployment.

Built and maintained as a **Clarity Lab** product.
