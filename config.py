import os
from datetime import timedelta
from dotenv import load_dotenv

# Load secret environment variables
load_dotenv()

class Config:
    # 1. Basic Flask Settings
    SECRET_KEY = os.environ.get('SECRET_KEY', 'default-fallback-secret-key-for-dev')
    PERMANENT_SESSION_LIFETIME = timedelta(days=1)
    
    # 2. Database Settings
    DB_NAME = os.environ.get('DB_NAME', 'attendance_db')
    DB_USER = os.environ.get('DB_USER', 'projectuser')
    DB_PASS = os.environ.get('DB_PASS', 'projectpass')
    DB_HOST = os.environ.get('DB_HOST', 'localhost')
    DB_PORT = os.environ.get('DB_PORT', '5432')
    
    SQLALCHEMY_DATABASE_URI = f'postgresql://{DB_USER}:{DB_PASS}@{DB_HOST}:{DB_PORT}/{DB_NAME}'
    SQLALCHEMY_TRACK_MODIFICATIONS = False
