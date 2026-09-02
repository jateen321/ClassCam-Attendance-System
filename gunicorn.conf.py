"""
gunicorn.conf.py — Production WSGI Server Configuration
========================================================
Loaded via `gunicorn -c gunicorn.conf.py wsgi:app`.

Every value can be overridden with an environment variable so the same image
works on a laptop and on a larger host without a rebuild.
"""

import multiprocessing
import os


def _int_env(name, default):
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


_cpu_count = multiprocessing.cpu_count()

# ── Socket ──────────────────────────────────────────────────────────────────
# Nginx proxies to web:8080; the compose healthcheck also hits this port.
bind = os.environ.get('GUNICORN_BIND', '0.0.0.0:8080')

# ── Worker model ────────────────────────────────────────────────────────────
# 'gthread' rather than 'sync': request handlers block on things that are not
# CPU work — face_executor.submit(...).result() and the synchronous SMTP send in
# utils/email.py. Threads let a worker serve other requests while one is parked.
worker_class = os.environ.get('GUNICORN_WORKER_CLASS', 'gthread')

# Deliberately NOT the usual (2 * CPU) + 1. Face recognition is CPU-bound and
# each worker process owns its own 2-thread face_executor, so the real parallel
# CPU load is workers * 2. Over-forking here just thrashes dlib.
workers = _int_env('GUNICORN_WORKERS', max(2, _cpu_count // 2))
threads = _int_env('GUNICORN_THREADS', 4)

# ── Timeouts ────────────────────────────────────────────────────────────────
# The default 30s kills workers mid-upload: a multi-photo batch runs HOG face
# detection plus encoding on every image before the response is written.
timeout = _int_env('GUNICORN_TIMEOUT', 120)
graceful_timeout = _int_env('GUNICORN_GRACEFUL_TIMEOUT', 30)
keepalive = _int_env('GUNICORN_KEEPALIVE', 5)

# ── Worker recycling ────────────────────────────────────────────────────────
# OpenCV/dlib buffers make long-lived workers grow. Recycle them, with jitter so
# they don't all restart on the same request.
max_requests = _int_env('GUNICORN_MAX_REQUESTS', 200)
max_requests_jitter = _int_env('GUNICORN_MAX_REQUESTS_JITTER', 40)

# ── Preloading ──────────────────────────────────────────────────────────────
# MUST stay False. utils/face.py and utils/email.py build ThreadPoolExecutors at
# import time, and threads do not survive fork() — preloading would hand every
# worker a pool whose threads don't exist, and photo processing would hang.
preload_app = False

# ── Proxy ───────────────────────────────────────────────────────────────────
# Nginx connects from the compose network, not 127.0.0.1, so trust it for the
# X-Forwarded-* headers ProxyFix reads in app/__init__.py.
forwarded_allow_ips = os.environ.get('GUNICORN_FORWARDED_ALLOW_IPS', '*')

# ── Logging ─────────────────────────────────────────────────────────────────
# '-' sends both streams to stdout/stderr for `docker compose logs -f web`.
accesslog = os.environ.get('GUNICORN_ACCESS_LOG', '-')
errorlog = os.environ.get('GUNICORN_ERROR_LOG', '-')
loglevel = os.environ.get('GUNICORN_LOG_LEVEL', 'info')
