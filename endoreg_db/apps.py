from django.apps import AppConfig
from django.db.backends.signals import connection_created

from endoreg_db.authz.settings import ensure_keycloak_settings


class EndoregDbConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "endoreg_db"

    def ready(self):
        """
        Performs application startup tasks when the Django app is fully loaded.

        This method imports media-related model modules to ensure they are registered
        and ready for use when the application starts.
        """
        ensure_keycloak_settings()
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
