"""Explicit collection capture; JSON input is read from stdin, never shell arguments."""

import sys

from django.contrib.auth.models import User
from django.core.exceptions import ObjectDoesNotExist, PermissionDenied, ValidationError
from django.core.management.base import BaseCommand, CommandError, CommandParser
from lx_dtypes.models.contracts.dtypes_record_persistence import (
    DtypesRecordPersistencePayload,
)

from endoreg_db.models.medical.patient.patient_examination import PatientExamination
from endoreg_db.models.other.clinical_reference_import import ClinicalReferenceImport
from endoreg_db.models.other.study import Study
from endoreg_db.services.centers.access import resolve_allowed_center_ids
from endoreg_db.services.studies.collections import collect_study_examination


class Command(BaseCommand):
    help = "Collect a study examination from stdin JSON with a fresh server-side package evaluation."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--owner", required=True, type=int)
        parser.add_argument("--study", required=True, type=int)
        parser.add_argument("--patient-examination", required=True, type=int)
        parser.add_argument("--module", required=True)
        parser.add_argument("--module-version", required=True)
        parser.add_argument("--report-template")

    def handle(self, *args: str, **options: object) -> None:
        module, version = options["module"], options["module_version"]
        template = options.get("report_template")
        if (
            not isinstance(module, str)
            or not isinstance(version, str)
            or (template is not None and not isinstance(template, str))
        ):
            raise CommandError(
                "Exact module/version and an optional template are required"
            )
        owner_id, study_id, examination_id = (
            options["owner"],
            options["study"],
            options["patient_examination"],
        )
        if not all(
            isinstance(value, int) for value in (owner_id, study_id, examination_id)
        ):
            raise CommandError("Owner, study and examination IDs must be integers")
        assert isinstance(examination_id, int)
        try:
            owner = User.objects.get(pk=owner_id, is_active=True)
            study = Study.objects.get(pk=study_id)
            centers = resolve_allowed_center_ids(owner)
            scope = PatientExamination.objects.all()
            if centers is not None:
                scope = scope.filter(patient__center_id__in=centers)
            raw = sys.stdin.read(10_000_001)
            if len(raw) > 10_000_000:
                raise CommandError("Collection JSON exceeds the input limit")
            record = DtypesRecordPersistencePayload.model_validate_json(raw)
            entry = collect_study_examination(
                owner=owner,
                study=study,
                examination_scope=scope,
                patient_examination_id=examination_id,
                package_import=ClinicalReferenceImport.objects.get(
                    module=module, version=version
                ),
                record=record,
                report_template_name=template,
            )
        except (
            ValueError,
            ValidationError,
            PermissionDenied,
            ObjectDoesNotExist,
        ) as error:
            raise CommandError(
                "Study collection failed validation or authorization; no entry committed"
            ) from error
        self.stdout.write(str(entry.uuid))
