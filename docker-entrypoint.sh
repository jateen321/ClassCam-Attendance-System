#!/bin/sh
# docker-entrypoint.sh — Container Startup
# =========================================
# Prepares the database in a separate, short-lived process, then hands the
# container over to Gunicorn. Running the migrations here (rather than inside
# the app) keeps them to a single execution no matter how many workers boot.
set -e

echo "[entrypoint] Preparing database..."
python init_db.py

echo "[entrypoint] Starting Gunicorn..."
exec gunicorn --config gunicorn.conf.py wsgi:app
