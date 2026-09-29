from io import StringIO
from unittest.mock import patch

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import OperationalError


def test_missing_schema_is_a_failure_without_import() -> None:
    with (
        patch(
            "endoreg_db.management.commands.import_reference_catalog.get_terminology_service"
        ) as service,
        patch(
            "endoreg_db.management.commands.import_reference_catalog.catalog_snapshot"
        ),
        patch("endoreg_db.management.commands.import_reference_catalog.select_catalog"),
        patch(
            "endoreg_db.management.commands.import_reference_catalog.plan_reference_catalog",
            side_effect=OperationalError("missing table"),
        ),
        patch(
            "endoreg_db.management.commands.import_reference_catalog.import_reference_catalog"
        ) as importer,
    ):
        service.return_value.load.return_value.config.name = "endoreg_reference"
        service.return_value.load.return_value.config.version = "1.0.0"
        with pytest.raises(CommandError, match="database migrations"):
            call_command(
                "import_reference_catalog",
                module="endoreg_reference",
                module_version="1.0.0",
                stdout=StringIO(),
            )
    importer.assert_not_called()
