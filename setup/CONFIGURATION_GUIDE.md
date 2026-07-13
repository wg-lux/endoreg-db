# EndoReg-DB Configuration and Environment Guide

This repository is a reusable Django app. It ships a small, robust settings package for local development and CI, while encouraging host projects to provide their own settings.

## Settings modules

- config/settings/base.py: shared defaults; driven by environment variables.
- config/settings/dev.py: local development; SQLite by default.
- config/settings/test.py: tests; persistent SQLite test DB by default.
- config/settings/prod.py: production defaults; fully env-driven.

Legacy settings (prod_settings.py, dev/dev_settings.py, tests/test_settings.py) are thin wrappers and can be removed after consumers update.

For a concise downstream upgrade checklist covering center identity, hub-role
transfer gating, and cleanup semantics, see
[`docs/deployment_note_hub_contract.md`](/home/admin/endoreg-db/docs/deployment_note_hub_contract.md).

## Centralized environment handling

- Use helpers in `endoreg_db/config/env.py` (env_str, env_bool, env_int, env_path).
- .env is not loaded during pytest to prevent test runs from picking up dev settings.
- Under pytest, `DJANGO_SETTINGS_MODULE` is forced to `endoreg_db.config.settings.test`.

## Key environment variables

General
- DJANGO_SETTINGS_MODULE: choose settings module (defaults used in manage.py/wsgi.py/pytest.ini).
- LX_ANNOTATE_ENCRYPTED_DATA_DIR: canonical protected runtime root. `STORAGE_DIR` must resolve inside this root.
- STORAGE_DIR: absolute path to protected managed media storage. Defaults to `${LX_ANNOTATE_ENCRYPTED_DATA_DIR}/storage`.
- STATIC_URL, STATIC_ROOT, MEDIA_URL: override static/media paths if embedding.

- TIME_ZONE: defaults to Europe/Berlin.

Path roles
- `endoreg_db/data/`: package-owned seed and setup data shipped with the app. Use this for YAML/bootstrap content loaded by commands such as `load_base_db_data`.
- `LX_ANNOTATE_ENCRYPTED_DATA_DIR`: single canonical protected runtime root. This is the top-level contract for deployment-owned data in this project.
- `STORAGE_DIR`: protected runtime-managed media and managed artifacts such as documents, processed videos, frames, and model weights.


Development (endoreg_db.config.settings.dev)
- DEV_DB_ENGINE: default django.db.backends.sqlite3
- DEV_DB_NAME: default BASE_DIR/dev_db.sqlite3
- DEV_DB_USER, DEV_DB_PASSWORD, DEV_DB_HOST, DEV_DB_PORT: used for non-SQLite engines.

Testing (endoreg_db.config.settings.test)
- TEST_DB_ENGINE: default django.db.backends.sqlite3
- TEST_DB_NAME: default data/tests/db/test_db.sqlite3
- TEST_DB_FILE: alternative way to set SQLite DB path
- TEST_DISABLE_MIGRATIONS: true|false (default false)

Production (endoreg_db.config.settings.prod)
- DJANGO_SECRET_KEY: required (must be a strong random value; never commit real secrets)
- DJANGO_DEBUG: true|false (use false in production)
- DJANGO_ALLOWED_HOSTS: comma-separated
- DB_ENGINE, DB_NAME, DB_USER, DB_PASSWORD, DB_HOST, DB_PORT
- ENDOREG_DEPLOYMENT_ROLE: `standalone`|`site_node`|`central_hub`. `central_hub` enables strict API ingest policy and exposes the transfer endpoints.
- ENDOREG_HUB_TRANSFER_REQUIRE_SECURE_TRANSPORT: true|false. Defaults to true. Refuses insecure transfer requests.
- ENDOREG_HUB_TRANSFER_REQUIRE_MTLS: true|false. Defaults to `true` for `central_hub` deployments and is required in production `central_hub` settings.
- ENDOREG_HUB_TRANSFER_MTLS_META_KEY, ENDOREG_HUB_TRANSFER_MTLS_META_VALUE: proxy-attested client-certificate verification contract for Django when mTLS is terminated before the app.
- SECURE_SSL_REDIRECT, SESSION_COOKIE_SECURE, CSRF_COOKIE_SECURE
- SECURE_HSTS_SECONDS, SECURE_HSTS_INCLUDE_SUBDOMAINS, SECURE_HSTS_PRELOAD

## Ingress modes

EndoReg-DB supports two ingress boundaries that converge on the same shared ingest services:

- `watcher`: trusted local filesystem ingestion. This path may resolve the default center when no center is declared.
- `api`: authenticated HTTP ingestion. This path creates `UploadJob` records just like watcher ingest, but it is intended for remote callers and stricter policy.

Both ingress modes feed the same upload job and processing model. The difference is the trust boundary, not the downstream pipeline.

## Workflow contract

The intended production workflow is:

1. A boundary adapter accepts input.
   `watcher` reads trusted local files.
   `api` accepts authenticated remote uploads.
   `transfer` accepts authenticated node-to-node synchronization only in `central_hub` deployments.
2. The boundary resolves center identity.
   `center_key` is the canonical machine-facing identifier.
   Human-readable center names remain display data only.
3. The boundary creates an `UploadJob` or `TransferJob`.
   Provenance is normalized at creation time so downstream logic sees one stable audit contract.
4. Shared ingest services process the artifact.
   Import, anonymization, case resolution, and media persistence happen in common service code rather than boundary-specific view logic.
5. Retention and cleanup policy determine post-success lifecycle.

Normalized upload provenance records:

- `entrypoint`
- `ingest_mode`
- `source_system`
- `source_center_key`
- `storage_class`
- `storage_tier`
- `retention_policy`

Normalized transfer provenance records:

- `entrypoint`
- `source_node_key`
- `target_node_key`
- `source_center_key`
- `transfer_mode`
- `processing_policy`
- `cleanup_policy`

Cleanup semantics are intentional:

- `preserve_source` upload jobs must not become cleanup-eligible on success
- `delete_after_success` upload jobs become cleanup-eligible on success
- `retain_all` transfer jobs record `not_requested`
- transfer cleanup requests other than `retain_all` record deferred cleanup intent and require explicit operational handling

## Transfer support

The package also includes a node-to-node transfer API under
`/api/media/hub/transfers/`.

- Default hub boundary: upload-job API ingest at `/api/upload/`
- Optional secondary boundary: transfer-job ingest at `/api/media/hub/transfers/`

Enable transfer support only when you are intentionally operating authenticated site-node to hub synchronization:

- set `ENDOREG_DEPLOYMENT_ROLE=central_hub`
- keep `ENDOREG_HUB_TRANSFER_REQUIRE_SECURE_TRANSPORT=true`
- require proxy-verified mTLS with `ENDOREG_HUB_TRANSFER_REQUIRE_MTLS=true`
- configure `ENDOREG_HUB_TRANSFER_MTLS_META_KEY` and `ENDOREG_HUB_TRANSFER_MTLS_META_VALUE`
- provision network node credentials
- keep normal API authentication and center scoping in place

Phase 1 transport protection for transfer support is:

- HTTPS or equivalent secure transport is mandatory
- node-authenticated transfer requests must present proxy-verified mTLS attestation
- `NetworkNode.shared_secret` remains request authentication only and does not replace transport security

If `ENDOREG_DEPLOYMENT_ROLE` is not `central_hub`, the transfer endpoints
return `404`. This is deliberate and prevents accidental exposure of a second
ingress boundary in non-hub deployments.

## Central Hub Role

Set `ENDOREG_DEPLOYMENT_ROLE=central_hub` when the package is deployed as a
shared multi-center ingest service rather than a local workstation or
single-site embedded app.

When `central_hub` is enabled:

- API uploads must be authenticated.
- API uploads must declare `center_key`.
- API uploads may not fall back to the default center.
- watcher ingestion remains supported and retains local default-center behavior.
- production configuration must use a non-SQLite database engine.

The central-hub role is intentionally strict. It makes remote ingestion fail
fast instead of guessing center identity from mutable names or local defaults.

## Hub deployment profile

For hub deployments, treat the following as required:

- PostgreSQL or another durable multi-user production database. SQLite is not acceptable in hub mode.
- Protected managed storage rooted under `LX_ANNOTATE_ENCRYPTED_DATA_DIR`, with `STORAGE_DIR` and inside that root.
- Durable shared or object-backed storage semantics for managed media and upload artifacts. Node-local ephemeral disks are not sufficient for a multi-node hub.
- Host-project encryption, backup, retention, and access-control controls around the managed storage root.
- OIDC/session or token authentication configured for API access in production.
- If transfer ingest is used, `ENDOREG_DEPLOYMENT_ROLE=central_hub` plus network-node secret management and rotation procedures.

This package provides the ingest and API contract, but the host deployment remains responsible for encrypted-at-rest guarantees and operational controls around the storage system.

## AI access contract

AI and automation clients should consume approved read APIs, not direct filesystem paths.

Preferred read surfaces:

- `GET /api/media/patients/{patient_id}/timeline/`
  Returns a center-scoped summary of the patient media timeline, including latest reports, latest videos, and frame stream URLs.
- `GET /api/media/pdfs/{id}/`
  Returns report metadata and anonymized text availability.
- `GET /api/media/pdfs/{id}/stream/?type=processed`
  Streams processed report media through the API boundary.
- `GET /api/media/videos/{id}/stream/?type=processed`
  Streams processed video media through the API boundary.
- `GET /api/media/videos/{video_id}/frames/{frame_number}/stream/`
  Streams extracted frames through the API boundary.

Center-scoped callers must only receive resources for their own center. The package now enforces center scoping on the core timeline and media read endpoints, so downstream AI services should rely on those APIs instead of `STORAGE_DIR` access.

## Typical usage patterns

As an embedded app in a host project:
- Add 'endoreg_db' to INSTALLED_APPS in the host settings.
- Define `LX_ANNOTATE_ENCRYPTED_DATA_DIR` in the host environment.
- Optionally override `STORAGE_DIR`, but keep it inside `LX_ANNOTATE_ENCRYPTED_DATA_DIR`.
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
- Development server: DJANGO_SETTINGS_MODULE=endoreg_db.config.settings.dev python manage.py runserver
- Tests (persistent test DB): pytest --reuse-db --create-db
- Clean test DB: rm -f data/tests/db/test_db.sqlite3

CI tips
- Use DJANGO_SETTINGS_MODULE=endoreg_db.config.settings.test
- First run use --create-db to run migrations once; subsequent runs can cache the database file.
- Override TEST_DB_NAME to a workspace cache path if needed.

## Direnv/Devenv
- Ensure devenv.nix and direnv don’t mutate repo files. Editor should inherit direnv env if used.

## Removing legacy settings
- Replace imports of prod_settings, dev/dev_settings.py, tests/test_settings.py with endoreg_db.config.settings.prod/dev/test.
- Run maintenance through the registered Django management commands with
  `DJANGO_SETTINGS_MODULE` set to `endoreg_db.config.settings.dev` or
  `endoreg_db.config.settings.test` as appropriate.

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
- For hub deployments, set `ENDOREG_DEPLOYMENT_ROLE=central_hub` and use PostgreSQL or another non-SQLite production database.
- Use the transfer endpoints only in `central_hub` deployments that intentionally support node-to-node synchronization.
- Keep `STORAGE_DIR` and inside `LX_ANNOTATE_ENCRYPTED_DATA_DIR`.
- For remote ingest, provision authentication before exposing `/api/upload/`.
