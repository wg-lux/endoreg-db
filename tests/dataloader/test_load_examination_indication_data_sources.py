from __future__ import annotations

from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from endoreg_db.management.commands import (
    load_examination_indication_data as indication_command_module,
)
from endoreg_db.models import (
    Examination,
    ExaminationIndication,
    ExaminationIndicationClassification,
    FindingIntervention,
)


class _FakeDtypesIndication:
    def __init__(
        self,
        *,
        description: str,
        interventions: list[str],
        indication_types: list[str],
    ) -> None:
        self.description = description
        self.interventions = interventions
        self.indication_types = indication_types


class _FakeDtypesIndicationType:
    def __init__(self, *, description: str) -> None:
        self.description = description


class _FakeDtypesExamination:
    def __init__(self, *, indications: list[str]) -> None:
        self.indications = indications


class _FakeDtypesKnowledgeBase:
    def __init__(self) -> None:
        self.indication = {
            "colonoscopy_screening": _FakeDtypesIndication(
                description="Screening indication from dtypes",
                interventions=[
                    "endoscopy_cold_snare_resection_generic",
                    "endoscopy_hemoclip_generic",
                    "missing_intervention",
                ],
                indication_types=["colonoscopy_screening_type"],
            )
        }
        self.indication_type = {
            "colonoscopy_screening_type": _FakeDtypesIndicationType(
                description="Type description from dtypes"
            )
        }
        self.examination = {
            "colonoscopy": _FakeDtypesExamination(indications=["colonoscopy_screening"])
        }


class LoadExaminationIndicationDataSourceTests(TestCase):
    def test_dtypes_source_upserts_and_syncs_links(self):
        Examination.objects.create(name="colonoscopy")
        FindingIntervention.objects.create(
            name="endoscopy_cold_snare_resection_generic"
        )
        FindingIntervention.objects.create(name="endoscopy_hemoclip_generic")

        fake_kb = _FakeDtypesKnowledgeBase()
        with patch(
            "endoreg_db.management.commands.load_examination_indication_data._load_dtypes_knowledge_base",
            return_value=fake_kb,
        ):
            call_command(
                "load_examination_indication_data",
                source="dtypes",
                module_name="fake_module",
            )

        indication = ExaminationIndication.objects.get(name="colonoscopy_screening")
        self.assertEqual(indication.description, "Screening indication from dtypes")
        self.assertSetEqual(
            set(indication.expected_interventions.values_list("name", flat=True)),
            {
                "endoscopy_cold_snare_resection_generic",
                "endoscopy_hemoclip_generic",
            },
        )
        self.assertSetEqual(
            set(indication.classifications.values_list("name", flat=True)),
            {"colonoscopy_screening_type"},
        )

        classification = ExaminationIndicationClassification.objects.get(
            name="colonoscopy_screening_type"
        )
        self.assertEqual(classification.description, "Type description from dtypes")

        exam = Examination.objects.get(name="colonoscopy")
        self.assertSetEqual(
            set(exam.indications.values_list("name", flat=True)),
            {"colonoscopy_screening"},
        )

    def test_hybrid_source_keeps_yaml_when_dtypes_missing(self):
        with (
            patch(
                "endoreg_db.management.commands.load_examination_indication_data.load_model_data_from_yaml"
            ) as mocked_yaml_loader,
            patch(
                "endoreg_db.management.commands.load_examination_indication_data._load_dtypes_knowledge_base",
                side_effect=RuntimeError("dtypes not available"),
            ),
        ):
            call_command(
                "load_examination_indication_data",
                source="hybrid",
                module_name="fake_module",
            )

        self.assertEqual(
            mocked_yaml_loader.call_count,
            len(indication_command_module.IMPORT_MODELS),
        )

    def test_dtypes_source_reports_command_error_on_loader_issue(self):
        with patch(
            "endoreg_db.management.commands.load_examination_indication_data._load_dtypes_knowledge_base",
            side_effect=RuntimeError("boom"),
        ):
            with self.assertRaises(CommandError):
                call_command(
                    "load_examination_indication_data",
                    source="dtypes",
                    module_name="fake_module",
                )
