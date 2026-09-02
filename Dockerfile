# ==========================================
# MULTI-STAGE BUILD
# ==========================================
# STAGE 1: "builder" — compiles dlib/face_recognition (needs gcc, cmake)
# STAGE 2: "runtime" — runs the app (NO compilers = much smaller)
# ==========================================

# ── STAGE 1: Build Stage ──────────────────────────────────────────────────
# Purpose: compile dlib (C++ library that face_recognition depends on)
# This stage is THROWN AWAY after we extract the compiled packages
FROM python:3.10-slim AS builder

# Install compilers (only needed to BUILD dlib, not to RUN it)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    cmake \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build

# Install Python packages into a virtual environment for clean isolation
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt


# ── STAGE 2: Runtime Stage ────────────────────────────────────────────────
# Purpose: run the app with ONLY the compiled packages
# NO gcc, NO cmake, NO build-essential = ~660MB saved
FROM python:3.10-slim AS runtime

# Install ONLY the runtime libraries (not the compilers!)
# libglib2.0-0 + libgl1 are needed by OpenCV at RUNTIME
RUN apt-get update && apt-get install -y --no-install-recommends \
    libglib2.0-0 \
    libgl1 \
    && rm -rf /var/lib/apt/lists/*

# Copy the pre-built virtual environment from the builder stage
# This contains all compiled .so files (dlib, numpy, opencv) but
# WITHOUT the compilers that built them
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

WORKDIR /app

# Copy application code
COPY . .

# Entrypoint runs the DB migrations, then execs Gunicorn
RUN chmod +x /app/docker-entrypoint.sh

EXPOSE 8080

CMD ["/app/docker-entrypoint.sh"]


# Test image used by CI. Production images continue to stop at the runtime stage.
FROM runtime AS test

RUN pip install --no-cache-dir -r requirements-dev.txt

# Override the entrypoint CMD — tests need neither a database nor a web server.
CMD ["python", "-m", "pytest"]


# Keep the default/final image free of development-only dependencies.
FROM runtime AS production

# Gunicorn (not the Flask dev server) serves production traffic.
# Tuning lives in gunicorn.conf.py and is overridable via GUNICORN_* env vars.
CMD ["/app/docker-entrypoint.sh"]
