"""
extensions.py — Neutral Ground
================================
WHY THIS FILE EXISTS:
  Flask extensions (db, login_manager, csrf) must be created WITHOUT the app
  object so that multiple files can import them without circular imports.
  
  Pattern:
    1. Create here  →  db = SQLAlchemy()          (no app!)
    2. Connect later →  db.init_app(app)           (inside create_app())

  If we did db = SQLAlchemy(app) here, then:
    - models.py would import db from here
    - __init__.py would import models
    - __init__.py also creates app
    → CIRCULAR IMPORT CRASH 💥
"""

from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from flask_login import LoginManager
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_wtf.csrf import CSRFProtect

# ── Create extensions WITHOUT app ──────────────────────────────────────────
db = SQLAlchemy()
migrate = Migrate(compare_type=True)
login_manager = LoginManager()
csrf = CSRFProtect()
limiter = Limiter(key_func=get_remote_address)
