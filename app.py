"""
app.py — Entry Point (Minimal)
================================
This file is now ONLY responsible for:
  1. Creating the app via the factory
  2. Starting the development server.

Database schema changes are applied separately with ``flask db upgrade``.

All routes, models, and logic live inside the app/ package.
"""

import os
import time
import logging
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

from app import create_app

# Create the app — this is what Gunicorn / Docker will use too
app = create_app()


def env_bool(name, default=False):
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


if __name__ == "__main__":
    debug_mode = env_bool("FLASK_DEBUG", False)
    use_reloader = env_bool("FLASK_USE_RELOADER", False)
    app.run(host='0.0.0.0', port=8080, debug=debug_mode, use_reloader=use_reloader)
