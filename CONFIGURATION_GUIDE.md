# EndoReg-DB Configuration and Environment Guide

This repository is a reusable Django app. It ships a small, robust settings package for local development and CI, while encouraging host projects to provide their own settings.

## Settings modules

- config/settings/base.py: shared defaults; driven by environment variables.
- config/settings/dev.py: local development; SQLite by default.
- config/settings/test.py: tests; persistent SQLite test DB by default.
- config/settings/prod.py: production defaults; fully env-driven.

Legacy settings (prod_settings.py, dev/dev_settings.py, tests/test_settings.py) are thin wrappers and can be removed after consumers update.

## Centralized environment handling

- Use helpers in `endoreg_db/config/env.py` (env_str, env_bool, env_int, env_path).
- .env is not loaded during pytest to prevent test runs from picking up dev settings.
- Under pytest, `DJANGO_SETTINGS_MODULE` is forced to `config.settings.test`.

## Key environment variables

General
- DJANGO_SETTINGS_MODULE: choose settings module (defaults used in manage.py/wsgi.py/pytest.ini).
- STORAGE_DIR: absolute path to media storage (defaults to storage/ in repo).
- STATIC_URL, STATIC_ROOT, MEDIA_URL: override static/media paths if embedding.
- TIME_ZONE: defaults to Europe/Berlin.

Development (config.settings.dev)
- DEV_DB_ENGINE: default django.db.backends.sqlite3
- DEV_DB_NAME: default BASE_DIR/dev_db.sqlite3
- DEV_DB_USER, DEV_DB_PASSWORD, DEV_DB_HOST, DEV_DB_PORT: used for non-SQLite engines.

Testing (config.settings.test)
- TEST_DB_ENGINE: default django.db.backends.sqlite3
- TEST_DB_NAME: default data/tests/db/test_db.sqlite3
- TEST_DB_FILE: alternative way to set SQLite DB path
- TEST_DISABLE_MIGRATIONS: true|false (default false)

Production (config.settings.prod)
- DJANGO_SECRET_KEY: required (must be a strong random value; never commit real secrets)
- DJANGO_DEBUG: true|false (use false in production)
- DJANGO_ALLOWED_HOSTS: comma-separated
- DB_ENGINE, DB_NAME, DB_USER, DB_PASSWORD, DB_HOST, DB_PORT
- SECURE_SSL_REDIRECT, SESSION_COOKIE_SECURE, CSRF_COOKIE_SECURE
- SECURE_HSTS_SECONDS, SECURE_HSTS_INCLUDE_SUBDOMAINS, SECURE_HSTS_PRELOAD

## Typical usage patterns

As an embedded app in a host project:
- Add 'endoreg_db' to INSTALLED_APPS in the host settings.
- Define STORAGE_DIR in the host environment.
- Run migrations in the host project (this app contributes its migrations).

This repo standalone (local):
- Development server: DJANGO_SETTINGS_MODULE=config.settings.dev python manage.py runserver
- Tests (persistent test DB): pytest --reuse-db --create-db
- Clean test DB: rm -f data/tests/db/test_db.sqlite3

CI tips
- Use DJANGO_SETTINGS_MODULE=config.settings.test
- First run use --create-db to run migrations once; subsequent runs can cache the database file.
- Override TEST_DB_NAME to a workspace cache path if needed.

## Direnv/Devenv
- Ensure devenv.nix and direnv don’t mutate repo files. Editor should inherit direnv env if used.

## Removing legacy settings
- Replace imports of prod_settings, dev/dev_settings.py, tests/test_settings.py with config.settings.prod/dev/test.
- Update scripts: scripts/django_setup.py, check_video_files.py, etc., to default to config.settings.dev/test.

## Troubleshooting
- If numpy/opencv import errors appear in VS Code discovery, ensure the editor inherits direnv env (Direnv extension) or use a pytest wrapper.
- If tests fail with missing tables, recreate test DB with: pytest --reuse-db --create-db.
- Run migrations and seed base data for tests when needed:
  - DJANGO_SETTINGS_MODULE=config.settings.test python manage.py migrate
  - DJANGO_SETTINGS_MODULE=config.settings.test python manage.py load_base_db_data

## Production checklist
- Set DJANGO_SECRET_KEY to a strong random value (never commit). 
- Set DJANGO_ALLOWED_HOSTS to your domains.
- Enforce HTTPS: SECURE_SSL_REDIRECT=true, cookie secure flags true.
- Consider HSTS: set SECURE_HSTS_SECONDS (e.g., 31536000) only when ready; include subdomains/preload as appropriate.