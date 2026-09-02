import os
import secrets
from datetime import timedelta
from dotenv import load_dotenv

# Load secret environment variables
load_dotenv()

class Config:
    # 1. Basic Flask Settings
    APP_ENV = os.environ.get('APP_ENV', 'development').strip().lower()
    SECRET_KEY = os.environ.get('SECRET_KEY')
    PERMANENT_SESSION_LIFETIME = timedelta(days=1)
    RATELIMIT_STORAGE_URI = os.environ.get('RATELIMIT_STORAGE_URI', 'memory://')
    RATELIMIT_HEADERS_ENABLED = True
    
    # 2. Database Settings
    DB_NAME = os.environ.get('DB_NAME', 'attendance_db')
    DB_USER = os.environ.get('DB_USER', 'projectuser')
    DB_PASS = os.environ.get('DB_PASS', 'projectpass')
    DB_HOST = os.environ.get('DB_HOST', 'localhost')
    DB_PORT = os.environ.get('DB_PORT', '5432')
    
    SQLALCHEMY_DATABASE_URI = f'postgresql://{DB_USER}:{DB_PASS}@{DB_HOST}:{DB_PORT}/{DB_NAME}'
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    @staticmethod
    def validate_security_configuration(app):
        """Require a durable, non-placeholder signing key in production."""
        secret_key = app.config.get('SECRET_KEY')
        insecure_values = {
            'change_this_to_a_strong_secret',
            'default-fallback-secret-key-for-dev',
            'secret',
        }

        if app.config.get('APP_ENV') == 'production':
            if (
                not isinstance(secret_key, str)
                or len(secret_key.strip()) < 32
                or secret_key.strip().lower() in insecure_values
            ):
                raise RuntimeError(
                    'SECRET_KEY must be a non-placeholder value of at least 32 characters when APP_ENV=production.'
                )
        elif not secret_key:
            # Development and tests do not silently use a publicly known key.
            app.config['SECRET_KEY'] = secrets.token_urlsafe(48)
