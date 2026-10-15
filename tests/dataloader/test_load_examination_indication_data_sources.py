from io import StringIO
from unittest.mock import patch

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError


@pytest.mark.django_db
def test_missing_pinned_package_never_falls_back_to_yaml() -> None:
    with (
        patch(
            "endoreg_db.management.reference_catalog_command.hydrate_shipped_terminology"
        ),
        patch(
            "endoreg_db.management.commands.import_reference_catalog.get_terminology_service"
        ) as service,
    ):
        service.return_value.load.side_effect = ValueError("missing pinned package")
        with pytest.raises(CommandError, match="failed validation"):
            call_command(
                "load_examination_indication_data",
                module="missing",
                module_version="1.0.0",
                stdout=StringIO(),
            )


@pytest.mark.parametrize("source", ["yaml", "hybrid", "dtypes"])
def test_legacy_source_switch_is_rejected(source: str) -> None:
    with pytest.raises(TypeError, match="Unknown option"):
        call_command("load_examination_indication_data", source=source)
