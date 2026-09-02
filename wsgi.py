"""WSGI entry point for the production Gunicorn server."""

from app import create_app


app = create_app()
