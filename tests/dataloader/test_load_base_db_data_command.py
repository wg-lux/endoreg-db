from io import StringIO
from unittest.mock import patch

from django.core.management import call_command


def test_bootstrap_delegates_to_one_pinned_catalogue_import() -> None:
    with (
        patch(
            "endoreg_db.management.reference_catalog_command.hydrate_shipped_terminology"
        ) as hydrate,
        patch(
            "endoreg_db.management.reference_catalog_command.call_command"
        ) as importer,
    ):
        call_command("load_base_db_data", stdout=StringIO())
    hydrate.assert_called_once_with()
    assert importer.call_args.args == ("import_reference_catalog",)
    assert importer.call_args.kwargs["module"] == "endoreg_reference"
    assert importer.call_args.kwargs["module_version"] == "1.0.0"
    assert importer.call_args.kwargs["record_types"] == []


def test_bootstrap_dry_run_does_not_hydrate_or_select_studies() -> None:
    with (
        patch(
            "endoreg_db.management.reference_catalog_command.hydrate_shipped_terminology"
        ) as hydrate,
        patch(
            "endoreg_db.management.reference_catalog_command.call_command"
        ) as importer,
    ):
        call_command(
            "load_base_db_data",
            module="selected",
            module_version="2.0.0",
            dry_run=True,
            stdout=StringIO(),
        )
    hydrate.assert_not_called()
    assert importer.call_args.kwargs["module"] == "selected"
    assert importer.call_args.kwargs["module_version"] == "2.0.0"
    assert importer.call_args.kwargs["dry_run"] is True
