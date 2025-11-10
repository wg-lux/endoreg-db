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
- Run the complete setup command: `python manage.py setup_endoreg_db`

The `setup_endoreg_db` command performs all necessary initialization:
1. Loads base database data (medical vocabularies, centers, etc.)
2. Creates Django cache table for API functionality (only when using database-backed caching)
3. Sets up AI models and labels (unless --skip-ai-setup is used)
4. Creates AI model metadata with weights
5. Verifies the setup was successful

The command automatically detects your cache configuration:
- For LocMemCache (default): Skips cache table creation
- For database caching: Creates the required cache tables

Use `--skip-ai-setup` if AI video processing features are not needed, or `--force-recreate` to recreate AI metadata.

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

## AI Model Setup (for video processing features)

When using EndoReg DB's AI-powered video processing features, ensure model weights are available:

### Model Weights Location
The system looks for model weights in these locations (in order of preference):
1. `STORAGE_DIR/model_weights/` (recommended for production)
2. `tests/assets/` (for development/testing)
3. `assets/` (fallback location)

### Required Model Files
For colonoscopy video processing, the following model file is required:
- `colo_segmentation_RegNetX800MF_6.safetensors` - Multilabel classification model for colonoscopy

### Automatic Setup
The `setup_endoreg_db` command automatically:
- Loads AI model definitions and labels
- Creates model metadata with weights
- Sets up the default AI model for video processing

### Manual Setup (if needed)
If automatic setup fails, run these commands individually:
```bash
python manage.py load_ai_model_data
python manage.py load_ai_model_label_data
python manage.py createcachetable
python manage.py create_multilabel_model_meta --model_name image_multilabel_classification_colonoscopy_default --model_meta_version 1 --image_classification_labelset_name multilabel_classification_colonoscopy_default
```

### Troubleshooting AI Setup
- **"Model file not found"**: Ensure model weights are in one of the expected locations
- **"No model metadata found"**: Run the setup commands or use `--force-recreate`
- **Import errors**: Check that the `EndoscopyProcessor` import fix is applied in `video_import.py`

## Production checklist
- Set DJANGO_SECRET_KEY to a strong random value (never commit). 
- Set DJANGO_ALLOWED_HOSTS to your domains.
- Enforce HTTPS: SECURE_SSL_REDIRECT=true, cookie secure flags true.
- Consider HSTS: set SECURE_HSTS_SECONDS (e.g., 31536000) only when ready; include subdomains/preload as appropriate.