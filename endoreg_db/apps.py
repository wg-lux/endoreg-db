import os
import sys
from pathlib import Path

from django.apps import AppConfig
from django.db.backends.signals import connection_created

from endoreg_db.authz.settings import ensure_keycloak_settings


class EndoregDbConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "endoreg_db"

    def ready(self):
        """
        Finalize app startup integration hooks.

        This currently ensures auth-related settings are normalized and, for
        runtime server entrypoints only, attaches the reconciliation service to
        Django's `connection_created` signal so startup repair runs after the
        first database connection becomes available.
        """
        ensure_keycloak_settings()
        from endoreg_db.utils.filesystem.paths import validate_runtime_storage_contract

        validate_runtime_storage_contract()
        executable = Path(sys.argv[0]).name if sys.argv else ""
        if (
            "pytest" in executable
            or executable == "py.test"
            or "PYTEST_CURRENT_TEST" in os.environ
            or len(sys.argv) < 2
        ):
            return
        runtime_commands = {
            "runserver",
            "run_gunicorn",
            "gunicorn",
            "uvicorn",
            "daphne",
        }
        if sys.argv[1] not in runtime_commands:
            return

        from endoreg_db.services.reconciliation import (
            ReconciliationService,
            should_run_startup_reconciliation,
        )

        if should_run_startup_reconciliation():

            def _run_reconciliation(**kwargs):
                ReconciliationService().run_once()

            connection_created.connect(
                _run_reconciliation,
                dispatch_uid="endoreg_db_startup_reconciliation",
            )
