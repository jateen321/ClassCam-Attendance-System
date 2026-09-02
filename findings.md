# Findings

- Database already had the legacy schema; stamped Alembic revision `20260902_0001`, then confirmed `flask db upgrade` succeeds with no pending migrations.
- Rebuilt the web image with current dependencies and confirmed the healthy container runs Gunicorn via `wsgi:app`.
- Development and production Compose files both validate successfully; development uses Flask debug mode, while production uses Gunicorn behind Nginx.
- Runtime check passed: web container is healthy under Gunicorn; Nginx HTTPS root returns `200 OK`. Port `8080` is pgAdmin in the legacy stack.
- Development runs with `docker-compose.dev.yml` at `http://localhost:8080`; stop the legacy stack first if pgAdmin is already occupying that port.
