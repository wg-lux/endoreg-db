from io import StringIO
import json

from django.core.management.base import CommandError
import pytest
import yaml

from endoreg_db.management.commands.import_clinical_reference_data import (
    Command as ClinicalCommand,
)
from endoreg_db.management.commands.import_reference_catalog import (
    Command as CatalogCommand,
)
from endoreg_db.services.reference_data.catalog import CatalogPlan


pytestmark = pytest.mark.no_db


@pytest.mark.parametrize("command_type", [ClinicalCommand, CatalogCommand])
@pytest.mark.parametrize("as_yaml", [False, True])
def test_reconciliation_output_preserves_conflicts_and_unicode(
    command_type: type[ClinicalCommand] | type[CatalogCommand], as_yaml: bool
) -> None:
    output = StringIO()
    command = command_type(stdout=output)
    payload = {
        "module": "clinical",
        "version": "1.0.0",
        "snapshot_id": "review",
        "projection": "clinical",
        "changes": [],
        "conflicts": ["Unvereinbare Änderung"],
        "clinical_plan": None,
        "unchanged": False,
    }
    plan = CatalogPlan.model_validate(payload)

    command.render_plan(plan, as_yaml=as_yaml)

    rendered = output.getvalue()
    assert "Unvereinbare Änderung" in rendered
    assert rendered.endswith("\n")
    assert (yaml.safe_load(rendered) if as_yaml else json.loads(rendered)) == payload
    assert not plan.can_import


@pytest.mark.parametrize("command_type", [ClinicalCommand, CatalogCommand])
@pytest.mark.parametrize(
    "arguments",
    [
        [],
        ["--module", "clinical"],
        ["--module", "clinical", "--module-version", "1.0.0", "--format", "xml"],
    ],
)
def test_reconciliation_options_reject_missing_identity_or_unknown_format(
    command_type: type[ClinicalCommand] | type[CatalogCommand], arguments: list[str]
) -> None:
    parser = command_type().create_parser("manage.py", "import")

    with pytest.raises(CommandError):
        parser.parse_args(arguments)
