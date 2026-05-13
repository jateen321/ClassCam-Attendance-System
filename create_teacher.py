import sys
import os
from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash
from datetime import datetime, timezone
import getpass

# Import from our modular structure
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

try:
    from app import create_app
    from app.models import Teacher
    from app.extensions import db
    from app.utils.db_helpers import ensure_default_admin
    app = create_app()
except ImportError as e:
    print(f"Error importing from app package: {e}")
    print("Please make sure this script is in the same directory as the app/ folder")
    sys.exit(1)


def create_super_admin():
    """
    Creates the initial Super Admin account.
    This account has the 'Admin' role and is pre-approved.
    """
    print("--- Create Super Admin Account ---")
    print("This account will have full administrative privileges.")

    # Non-interactive creation using environment variables (preferred for Docker)
    env_email = os.environ.get("DEFAULT_ADMIN_EMAIL")
    env_password = os.environ.get("DEFAULT_ADMIN_PASSWORD")
    if env_email and env_password:
        try:
            ensure_default_admin(app)
            print(f"Admin account created/verified from environment variables.")
        except Exception as e:
            print(f"Failed to create Admin from environment variables: {e}")
        return

    # Interactive fallback (only used when env vars are missing)
    username = input("Enter Admin username: ").strip()
    email = input("Enter Admin email: ").strip()

    # Validate email domain
    if not email.endswith('@iitj.ac.in'):
        print("Invalid email domain. Only '@iitj.ac.in' emails are allowed. Aborting.")
        return
    
    # Get password securely
    password = getpass.getpass("Enter Admin password (min 6 chars): ").strip()
    confirm_password = getpass.getpass("Confirm Admin password: ").strip()

    if password != confirm_password:
        print("Passwords do not match. Aborting.")
        return

    if len(password) < 6:
        print("Password must be at least 6 characters. Aborting.")
        return
        
    if not username or not email:
        print("Username and email cannot be empty. Aborting.")
        return
        
    try:
        with app.app_context():
            if Teacher.query.filter_by(email=email).first():
                print(f"Admin with email '{email}' already exists.")
                return
            new_admin = Teacher(username=username, email=email, role='Admin', is_approved=True)
            new_admin.set_password(password)
            db.session.add(new_admin)
            db.session.commit()
            print(f"\nSuccessfully created Admin account for '{username}'.")
            print("You can now run 'python app.py' and log in.")
    except Exception as e:
        print(f"\nAn error occurred: {e}")
        if "violates unique constraint" in str(e).lower():
            print("This username or email already exists in the database.")

if __name__ == "__main__":
    try:
        create_super_admin()
    except Exception as e:
        print(f"A fatal error occurred: {e}")
