"""
init_db.py — One-Shot Database Initialisation
===============================================
Waits for Postgres, creates/migrates the schema, and seeds the default admin.

Run this ONCE per container start, in its own process, BEFORE the web server
boots. It must not run inside a Gunicorn worker: workers are forked per request
capacity, and several of them running `db.create_all()` plus the ALTER TABLE
migrations concurrently would race on the same DDL.

Usage:
    python init_db.py          # exits 0 on success, 1 on failure
"""

import logging
import sys

from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


def initialise_database():
    """Prepare the database for the app. Returns True on success."""
    from app import create_app
    from app.extensions import db
    from app.utils.db_helpers import ensure_default_admin, setup_database, wait_for_database

    if not wait_for_database():
        return False

    app = create_app()
    try:
        setup_database(app)
        ensure_default_admin(app)
    except Exception as err:
        logger.error(f"Database initialisation failed: {err}")
        return False
    finally:
        # Drop pooled connections so nothing is left open for the web server
        # process that starts after us.
        with app.app_context():
            db.engine.dispose()

    logger.info("Database initialisation complete.")
    return True


if __name__ == "__main__":
    sys.exit(0 if initialise_database() else 1)
