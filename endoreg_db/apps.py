import os
import sys
from importlib import import_module
from pathlib import Path
from typing import Protocol, cast
from logging import getLogger
from django.apps import AppConfig
from django.core.exceptions import ImproperlyConfigured
from django.db.backends.base.base import BaseDatabaseWrapper
from django.db.backends.signals import connection_created
from django.dispatch import Signal

from endoreg_db.authz.settings import ensure_keycloak_settings

logger = getLogger(__name__)


class _ConnectionCreatedReceiver(Protocol):
    def __call__(
        self,
        *,
        signal: Signal,
        sender: type[BaseDatabaseWrapper],
        connection: BaseDatabaseWrapper,
        **kwargs: object,
    ) -> None: ...


class _ConnectionCreatedSignal(Protocol):
    def connect(
        self,
        receiver: _ConnectionCreatedReceiver,
        dispatch_uid: str,
    ) -> None: ...


class EndoregDbConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "endoreg_db"
    _terminology_initialized: bool = False

    def _initialize_terminology(self) -> None:
        """Provision and validate terminology before completing startup."""
        if self._terminology_initialized:
            return

        from lx_dtypes.terminology.terminology_loader import (
            get_terminology_service,
        )
        from lx_dtypes.terminology.terminology_service import TerminologyError

        service = get_terminology_service()
        logger.info(
            "Initializing terminology registry: %s",
            service.registry_path,
        )

        try:
            service.provision()

            identity = service.active_identity()
            if identity is None:
                raise ImproperlyConfigured(
                    "Terminology registry has no active bundle: "
                    f"{service.registry_path}"
                )

            # Verify that the selected identity is actually loadable.
            service.load(*identity)

        except TerminologyError as exc:
            raise ImproperlyConfigured(
                f"Terminology startup failed for {service.registry_path}: {exc}"
            ) from exc

        # Only record success after both provisioning and loading succeed.
        self._terminology_initialized = True

        logger.info(
            "Terminology ready: %s@%s; registry=%s",
            identity[0],
            identity[1],
            service.registry_path,
        )

    def ready(self) -> None:
        """
        Finalize app startup integration hooks.

        This currently ensures auth-related settings are normalized and, for
        runtime server entrypoints only, attaches the reconciliation service to
        Django's `connection_created` signal so startup repair runs after the
        first database connection becomes available.
        """
        ensure_keycloak_settings()
        import_module("endoreg_db.checks")

        from endoreg_db.celery_observability import register_celery_timing_signals

        register_celery_timing_signals()

        from endoreg_db.utils.paths import validate_runtime_storage_contract

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

            def _run_reconciliation(
                *,
                signal: Signal,
                sender: type[BaseDatabaseWrapper],
                connection: BaseDatabaseWrapper,
                **kwargs: object,
            ) -> None:
                ReconciliationService().run_once()

            typed_connection_created = cast(
                _ConnectionCreatedSignal, connection_created
            )
            typed_connection_created.connect(
                _run_reconciliation,
                dispatch_uid="endoreg_db_startup_reconciliation",
            )
