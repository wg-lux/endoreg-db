import logging
import tempfile
import textwrap
from pathlib import Path
from unittest.mock import patch

import yaml
from django.core.management import call_command
from django.test import TestCase

from endoreg_db.data import REQUIREMENT_DATA_DIR
from endoreg_db.management.commands import load_requirement_data as load_requirement_command
from endoreg_db.management.commands.load_requirement_data import (
    IMPORT_METADATA,
)
from endoreg_db.management.commands.load_requirement_data import (
    Command as LoadRequirementCommand,
)
from endoreg_db.models import (
    Requirement,
    RequirementOperator,
    RequirementType,
)
from endoreg_db.utils import load_model_data_from_yaml

from ..helpers.data_loader import load_data
from ..helpers.default_objects import generate_patient

logger = logging.getLogger(__name__)
logger.setLevel(logging.WARNING)


class RequirementTest(TestCase):
    def setUp(self):
        load_data()

        self.patient = generate_patient()
        self.patient.save()

        self.requirements = Requirement.objects.all()
        self.assertGreater(len(self.requirements), 0, "No requirements found. Check data fixtures.")

    def test_requirements_have_type(self):
        # fetch all requirements and make sure they are linked to at least one requirement_type
        for req in self.requirements:
            logger.info(f"Testing requirement: {req.name}")
            self.assertTrue(req.requirement_types.exists(), f"Requirement '{req.name}' should have a linked RequirementType. Check data fixtures.")


class RequirementLoaderValidationTests(TestCase):
    def _load_yaml(self, yaml_text: str):
        with tempfile.TemporaryDirectory() as tmpdir:
            fixture_path = Path(tmpdir) / "requirement.yaml"
            fixture_path.write_text(textwrap.dedent(yaml_text).strip() + "\n", encoding="utf-8")

            metadata = dict(IMPORT_METADATA[Requirement.__name__])
            metadata["dir"] = tmpdir
            metadata["foreign_keys"] = list(metadata["foreign_keys"])
            metadata["foreign_key_models"] = list(metadata["foreign_key_models"])
            metadata["validators"] = list(metadata.get("validators", []))

            command = LoadRequirementCommand()
            load_model_data_from_yaml(command, Requirement.__name__, metadata, verbose=False)

    def test_loader_rejects_requirement_without_operators(self):
        RequirementType.objects.get_or_create(name="patient")

        with self.assertRaisesRegex(ValueError, "operators"):
            self._load_yaml(
                """
                - model: endoreg_db.requirement
                  fields:
                    name: "req-missing-operators"
                    requirement_types: ["patient"]
                """
            )

    def test_loader_rejects_requirement_without_requirement_types(self):
        RequirementOperator.objects.get_or_create(name="models_match_any")

        with self.assertRaisesRegex(ValueError, "requirement_types"):
            self._load_yaml(
                """
                - model: endoreg_db.requirement
                  fields:
                    name: "req-missing-types"
                    operators: ["models_match_any"]
                """
            )

    def test_loader_accepts_requirement_with_required_configuration(self):
        RequirementType.objects.get_or_create(name="patient")
        RequirementOperator.objects.get_or_create(name="models_match_any")

        self._load_yaml(
            """
            - model: endoreg_db.requirement
              fields:
                name: "req-valid"
                requirement_types: ["patient"]
                operators: ["models_match_any"]
            """
        )

        self.assertTrue(
            Requirement.objects.filter(name="req-valid").exists(),
            "Loader should persist requirements when configuration is complete.",
        )


class RequirementFixtureAuditTests(TestCase):
    def test_all_requirement_fixtures_declare_types_and_operators(self):
        missing_entries: list[str] = []
        for yaml_path in sorted(REQUIREMENT_DATA_DIR.glob("*.yaml")):
            raw_content = yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or []
            if not isinstance(raw_content, list):
                continue

            for entry in raw_content:
                if not isinstance(entry, dict):
                    continue
                fields = entry.get("fields") or {}
                name = fields.get("name") or entry.get("pk") or f"<unnamed> in {yaml_path.name}"

                for key in ("requirement_types", "operators"):
                    values = fields.get(key)
                    if not isinstance(values, list) or not values or any(not item for item in values):
                        missing_entries.append(f"{yaml_path.name}:{name} missing {key}")

        if missing_entries:
            formatted = "\n".join(sorted(missing_entries))
            self.fail("Requirement fixtures are missing required configuration values:\n" + formatted)

    def test_management_command_flags_missing_configuration(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_dir = Path(tmpdir)
            bad_fixture = [
                {
                    "model": "endoreg_db.requirement",
                    "fields": {
                        "name": "invalid-fixture",
                        "requirement_types": [],
                        "operators": [],
                    },
                }
            ]
            (tmp_dir / "bad.yaml").write_text(
                yaml.safe_dump(bad_fixture, sort_keys=False),
                encoding="utf-8",
            )

            metadata_override = dict(load_requirement_command.IMPORT_METADATA)
            requirement_metadata = dict(metadata_override[Requirement.__name__])
            requirement_metadata["dir"] = tmp_dir
            metadata_override[Requirement.__name__] = requirement_metadata

            with (
                patch.object(load_requirement_command, "IMPORT_METADATA", metadata_override),
                patch.object(
                    load_requirement_command,
                    "IMPORT_MODELS",
                    [Requirement.__name__],
                ),
            ):
                with self.assertRaises(ValueError) as exc:
                    call_command("load_requirement_data")

        message = str(exc.exception)
        self.assertIn("missing required configuration", message)
        self.assertIn("requirement_types", message)
        self.assertIn("operators", message)
