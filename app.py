"""
app.py — Local Development Server
===================================
Runs the Flask dev server for local, non-Docker work:

    python app.py

This is NOT how the app is served in Docker or in production — Gunicorn loads
`wsgi:app` instead (see gunicorn.conf.py and docker-entrypoint.sh). The Flask
dev server is single-threaded and unsuitable for real traffic.

All routes, models, and logic live inside the app/ package.
"""

import os

from wsgi import app  # noqa: F401  — re-exported so `python app.py` serves the same app


def env_bool(name, default=False):
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


if __name__ == "__main__":
    # In Docker this never runs: docker-entrypoint.sh calls init_db.py and then
    # execs Gunicorn. Locally we still need the schema in place first.
    from init_db import initialise_database

    if not initialise_database():
        raise SystemExit("Database initialisation failed — see the log above.")

    debug_mode = env_bool("FLASK_DEBUG", False)
    use_reloader = env_bool("FLASK_USE_RELOADER", False)
    app.run(host='0.0.0.0', port=8080, debug=debug_mode, use_reloader=use_reloader)
