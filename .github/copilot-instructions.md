## Big Picture
- **Core app**: Django project lives in `endoreg_db/`, `config/settings/*` drive environments via `endoreg_db.config.env`; root URLs mount the entire API under `/api/`.
- **Domain**: Focus on ingesting and anonymizing endoscopy videos and clinical reports; `services/video_import.py` handles the full pipeline, mirrored by management commands for CLI workflows.
- **Data layout**: Working assets stay under `storage/` (media root) and `data/` (raw/test fixtures); `endoreg_db/utils/paths.py` centralizes directories—respect existing folder contracts when writing files.
- **Knowledge base**: Medical vocabularies live in YAML under `endoreg_db/data/`; management commands in `endoreg_db/management/commands/load_*` hydrate models from those files—keep YAML schema changes aligned with the commands.
- **Async flows**: `celery_app.py` wires Celery but operational imports use Redis Queue (`endoreg_db/tasks/video_ingest.py`); align new background work with that queue unless migrating everything to Celery.

## Django Db Model Type Hinting:
- **TYPE_CHECKING**: Use `if TYPE_CHECKING:` blocks to declare model attributes for type checkers without runtime overhead.

**Implementation Examples**:
```python
from django.db import models
from typing import TYPE_CHECKING, cast

class ExampleClass1(models.Model):
    name = models.CharField(max_length=255, unique=True)
    description = models.TextField(blank=True, null=True)

    # One-to-many relationship
    example_1 = models.ForeignKey(
        "ExampleClass2",
        on_delete=models.CASCADE,
        related_name="example_class_1_set",
    )

    # One-to-one relationship
    example_2 = models.OneToOneField(
        "ExampleClass2",
        on_delete=models.CASCADE,
        related_name="example_class_1_one_to_one",
    )

    # Many to Many
    example_3 = models.ManyToManyField(
        "ExampleClass2",
        related_name="example_class_1_many_to_many_set",
    )

    # Nullable ForeignKey
    example_4 = models.ForeignKey(
        "ExampleClass2",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="example_class_1_nullable_foreign_key_set",
    )

    if TYPE_CHECKING:

        # One-to-manyrelationship
        example_1: models.ForeignKey["ExampleClass2"]

        # One-to-one relationship
        example_2: models.OneToOneField["ExampleClass2"]

        # Many to Many
        example_3 = cast(models.manager.RelatedManager["ExampleClass2"], example_3)

        # Nullable ForeignKey
        example_4: models.ForeignKey["ExampleClass2|None"]

class ExampleClass2(models.Model):
    name = models.CharField(max_length=255)

    if TYPE_CHECKING:
        # Reverse relation for one-to-many relationship
        @property
        def example_class_1_set(self) -> models.RelatedManager["ExampleClass1"]: ...

        # Reverse relation for one-to-one relationship
        example_class_1_one_to_one: models.OneToOneField["ExampleClass1"]

        # Reverse relation for many-to-many relationship
        @property
        def example_class_1_many_to_many_set(self) -> models.RelatedManager["ExampleClass1"]: ...

        # Reverse relation for nullable ForeignKey
        @property
        def example_class_1_nullable_foreign_key_set(self) -> models.RelatedManager["ExampleClass1"]: ... 

```
## Environment & Tooling
- **Platform baseline**: Most development happens on NixOS; run `devenv up` (or allow via `direnv`) before any command so the pinned toolchain and `uv` environment from `devenv.nix` activate.
- **Dev shell**: Once inside the shell, use `devenv task run env:build` to refresh `.env` via `env_setup.py`; the task relies on `.devenv-vars.json` generated during shell entry.
- **Python entry**: Always call `uv run ...` (e.g. `uv run python manage.py migrate`) so dependencies resolve inside `.devenv/state/venv`.
- **Settings switch**: `DJANGO_SETTINGS_MODULE` defaults to `config.settings.dev`; tests use `config.settings.test` which persists an SQLite DB at `data/tests/db/test_db.sqlite3`.
- **External repos**: LX anonymizer support is expected (`lx-anonymizer>=0.8.2.1`); the Nix enter hook can clone it—check this before debugging anonymization failures.

## Testing
- **Copilot test instructions**: Use `uv run pytest` to run tests since other test discovery causes issues
- **Test runner**: Prefer `uv run python runtests.py` to execute curated suites; arguments map to subpackages under `tests/` (e.g. `runtests helpers`).
- **Pytest direct**: For ad-hoc cases run `uv run pytest tests/path::TestClass -k keyword`; `pytest.ini` already points at the Django settings.
- **Video guards**: GPU/ffmpeg-heavy specs are behind `RUN_VIDEO_TESTS`; default false—set `RUN_VIDEO_TESTS=true` when required assets in `tests/assets/` are available.
- **Fixtures**: `tests/conftest.py` configures storage dirs and shared fixtures; extend `endoreg_db/factories/` instead of hand-building ORM objects in tests.

## Data & Management Commands
- **Database seed**: `load_base_db_data` (and other `load_*` commands) populate clinical taxonomies from CSVs under `data/` and `models_table.*`.
- **Knowledge YAML**: Commands like `load_disease_data`, `load_finding_data`, etc. expect curated YAML in `endoreg_db/data/`; keep keys stable and update factories/tests when the YAML schema shifts.
- **Import scripts**: Video/report ingestion commands delegate to `services/video_import.py` and `services/pdf_import.py`; reuse the service layer when adding new commands.
- **Backups**: `export_db.sh` and `import_db.sh` wrap Django `dumpdata/loaddata`—update them instead of reinventing backup flows.
- **Paths**: `env_setup.py` writes `STORAGE_DIR` into `.env`; new code should resolve paths via `endoreg_db.utils.data_paths` or `config.env_path` to stay relocatable.

## API & Presentation
- **Routing**: `endoreg_db/root_urls.py` includes `endoreg_db/urls/`; keep new REST endpoints inside `endoreg_db/api/views` + serializers and register them in `api_urls.py`.
- **Translations**: `django-modeltranslation` is preloaded (languages `de`/`en`); text fields need `TranslationOptions` under `endoreg_db/models/**/translation*.py`.
- **Forms/Admin**: Custom admin and form logic lives in `endoreg_db/admin.py` and `endoreg_db/forms/`; follow existing Bootstrap 5 widgets in `endoreg_db/templates/`.
- **Security**: Sensitive metadata models in `endoreg_db/models/metadata/` use state objects—preserve their transitions (see `VideoImportService._process_frames_and_metadata`).

## Observability & Utilities
- **Logging**: `config/settings/base.py` defines `TEST_LOGGER_NAMES`; use `logging.getLogger(__name__)` so unit tests capture module-level logs consistently.
- **Pipelines**: `endoreg_db/utils/pipelines/process_video_dir.py` shows the orchestration pattern—refresh model state after `pipe_1/pipe_2` and reuse its helpers for batch jobs.
- **Permissions**: Storage-aware helpers live in `endoreg_db/utils/permissions.py` and related services—reuse them instead of manual `os.path` checks.
- **Path hygiene**: File movement bugs usually stem from bypassing `endoreg_db.utils.paths` or `endoreg_db.config.env`; always derive locations from `data_paths[...]`/`env_path(...)` so raw/anonymized video & PDF paths stay consistent.
- **RQ usage**: Enqueue jobs via `endoreg_db/tasks/video_ingest.enqueue_video_import`; ensure `RQ_REDIS_URL` is configured and Django is initialized before background execution.
