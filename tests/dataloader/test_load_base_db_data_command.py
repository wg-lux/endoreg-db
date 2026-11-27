from io import StringIO
from unittest.mock import patch
import pytest

from django.core.management import call_command
from django.core.management.base import CommandError
from lx_dtypes.models.contracts.knowledge_base import KnowledgeBaseIdentity

from endoreg_db.helpers import data_loader
from endoreg_db.config.settings import dataloader
from tests.helpers import data_loader as test_loader


@pytest.mark.parametrize(
    "command",
    ["load_examination_data", "load_finding_data", "load_examination_indication_data"],
)
@pytest.mark.parametrize(
    "module,version",
    [("coloreg", "0.3.0"), ("selected", "0.3.0"), ("coloreg", "0.4.0")],
)
def test_test_helpers_use_central_package(
    command: str, module: str, version: str
) -> None:
    package = KnowledgeBaseIdentity(
        knowledge_base_module=module, knowledge_base_version=version
    )
    with (
        patch.object(dataloader, "CLINICAL_PACKAGE", package),
        patch.object(data_loader, "call_command") as importer,
    ):
        getattr(test_loader, command)()
    importer.assert_called_once_with(command, module=module, module_version=version)


def test_bulk_helper_only_bootstraps_application_packages() -> None:
    with patch.object(data_loader, "call_command") as command:
        data_loader.load_data()
    assert [call.args[0] for call in command.call_args_list] == [
        "load_base_db_data",
        "load_green_endoscopy_wuerzburg_data",
    ]


@pytest.mark.parametrize(
    "command",
    ["load_examination_data", "load_finding_data", "load_examination_indication_data"],
)
def test_clinical_helpers_forward_selected_package(command: str) -> None:
    package = KnowledgeBaseIdentity(
        knowledge_base_module="coloreg", knowledge_base_version="0.3.0"
    )
    with patch.object(data_loader, "call_command") as importer:
        getattr(data_loader, command)(package)
    importer.assert_called_once_with(command, module="coloreg", module_version="0.3.0")


@pytest.mark.parametrize(
    "command",
    ["load_examination_data", "load_finding_data", "load_examination_indication_data"],
)
def test_clinical_loaders_require_explicit_package_before_hydration(
    command: str,
) -> None:
    with patch(
        "endoreg_db.management.reference_catalog_command.hydrate_shipped_terminology"
    ) as hydrate:
        with pytest.raises(CommandError, match="required"):
            call_command(command, stdout=StringIO())
        hydrate.assert_not_called()


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
    assert importer.call_args.kwargs["module_version"] == "2.0.0"
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
