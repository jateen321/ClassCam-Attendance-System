"""
wsgi.py — WSGI Entry Point
============================
The callable Gunicorn loads: `gunicorn -c gunicorn.conf.py wsgi:app`.

WHY THIS FILE EXISTS (and why it isn't app.py):
  `app/` is a package and `app.py` is a module, both named "app". The package
  wins on sys.path, so `gunicorn app:app` resolves to app/__init__.py — which
  exports create_app(), not an `app` object — and the workers die with
  "Failed to find attribute 'app' in 'app'". A separate, unambiguous module name
  is the fix.

NOTE: schema creation and admin seeding do NOT happen here. This module is
imported once per Gunicorn worker, so migrations run from it would have every
worker race on the same DDL. init_db.py does that once, before Gunicorn starts.
"""

import logging

from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

from app import create_app

app = create_app()
