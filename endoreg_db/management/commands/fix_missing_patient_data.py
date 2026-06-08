"""
Management command to fix missing patient data in existing videos.
Fills in default values for videos that have incomplete SensitiveMeta.
"""

from datetime import date
from types import NoneType
from typing import Protocol, TypeAlias, TypedDict, Unpack, cast

from django.core.management.base import BaseCommand, CommandParser
from django.db import transaction
from lx_dtypes.models.contracts import (
    SensitiveMetaPatientRepairCreatePayload,
    SensitiveMetaPatientRepairData,
    SensitiveMetaPatientRepairUpdatePayload,
    dump_sensitive_meta_patient_repair_create_payload,
    dump_sensitive_meta_patient_repair_update_payload,
)

from endoreg_db.models import SensitiveMeta, VideoFile

JsonNull: TypeAlias = NoneType


class FixMissingPatientDataOptions(TypedDict):
    dry_run: bool
    verbose: bool


class _NamedCenter(Protocol):
    name: str


class _SensitiveMetaRepairInstance(Protocol):
    patient_first_name: str | JsonNull
    patient_last_name: str | JsonNull
    patient_dob: date | JsonNull
    examination_date: date | JsonNull

    def update_from_dict(self, data: SensitiveMetaPatientRepairData) -> None: ...


class _SensitiveMetaFactory(Protocol):
    def create_from_dict(
        self,
        data: SensitiveMetaPatientRepairData,
    ) -> _SensitiveMetaRepairInstance: ...


class _RepairVideo(Protocol):
    video_hash: str
    center: _NamedCenter | JsonNull
    sensitive_meta: _SensitiveMetaRepairInstance | JsonNull

    def save(self, *, update_fields: list[str]) -> None: ...


def _center_name(video: _RepairVideo) -> str:
    center = video.center
    if center is None:
        return "university_hospital_wuerzburg"
    return center.name


def _missing_patient_update(
    sensitive_meta: _SensitiveMetaRepairInstance,
) -> tuple[SensitiveMetaPatientRepairData, list[str]]:
    missing_fields: list[str] = []
    payload = SensitiveMetaPatientRepairUpdatePayload()

    if not sensitive_meta.patient_first_name:
        payload = payload.model_copy(update={"patient_first_name": "Patient"})
        missing_fields.append("first_name")

    if not sensitive_meta.patient_last_name:
        payload = payload.model_copy(update={"patient_last_name": "Unknown"})
        missing_fields.append("last_name")

    if not sensitive_meta.patient_dob:
        payload = payload.model_copy(update={"patient_dob": date(1990, 1, 1)})
        missing_fields.append("dob")

    if not sensitive_meta.examination_date:
        payload = payload.model_copy(update={"examination_date": date.today()})
        missing_fields.append("examination_date")

    return dump_sensitive_meta_patient_repair_update_payload(payload), missing_fields


class Command(BaseCommand):
    help = """
    Fixes missing patient data in existing VideoFile entries.
    This command will:
    1. Find all videos with missing or incomplete SensitiveMeta
    2. Create default SensitiveMeta for videos without any
    3. Fill in missing fields (first_name, last_name, DOB, examination_date)
    """

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show what would be changed without making actual changes",
        )
        parser.add_argument(
            "--verbose",
            action="store_true",
            help="Show detailed output",
        )

    def handle(
        self,
        *args: str,
        **options: Unpack[FixMissingPatientDataOptions],
    ) -> None:
        dry_run = options["dry_run"]
        verbose = options["verbose"]

        self.stdout.write(self.style.SUCCESS("Starting patient data repair..."))

        if dry_run:
            self.stdout.write(
                self.style.WARNING("DRY RUN MODE - No changes will be made")
            )

        # Find videos without SensitiveMeta
        videos_without_meta = VideoFile.objects.filter(sensitive_meta__isnull=True)
        count_without_meta = videos_without_meta.count()

        # Find videos with incomplete SensitiveMeta
        videos_with_incomplete_meta = (
            VideoFile.objects.filter(sensitive_meta__isnull=False).filter(
                # At least one of these fields is missing
                sensitive_meta__patient_first_name__isnull=True
            )
            | VideoFile.objects.filter(
                sensitive_meta__isnull=False,
                sensitive_meta__patient_last_name__isnull=True,
            )
            | VideoFile.objects.filter(
                sensitive_meta__isnull=False, sensitive_meta__patient_dob__isnull=True
            )
            | VideoFile.objects.filter(
                sensitive_meta__isnull=False,
                sensitive_meta__examination_date__isnull=True,
            )
            | VideoFile.objects.filter(
                sensitive_meta__isnull=False,
                sensitive_meta__patient_first_name__exact="",
            )
            | VideoFile.objects.filter(
                sensitive_meta__isnull=False,
                sensitive_meta__patient_last_name__exact="",
            )
        )

        count_incomplete = videos_with_incomplete_meta.count()

        self.stdout.write(f"Found {count_without_meta} videos without SensitiveMeta")
        self.stdout.write(
            f"Found {count_incomplete} videos with incomplete SensitiveMeta"
        )

        if count_without_meta == 0 and count_incomplete == 0:
            self.stdout.write(
                self.style.SUCCESS(
                    "No repairs needed - all videos have complete patient data!"
                )
            )
            return

        fixed_count = 0
        created_count = 0

        # Process videos without SensitiveMeta
        if count_without_meta > 0:
            self.stdout.write(
                f"\nProcessing {count_without_meta} videos without SensitiveMeta..."
            )

            sensitive_meta_factory = cast(_SensitiveMetaFactory, SensitiveMeta)

            for raw_video in videos_without_meta:
                video = cast(_RepairVideo, raw_video)
                if verbose:
                    self.stdout.write(
                        f"Creating SensitiveMeta for video {video.video_hash}"
                    )

                if not dry_run:
                    try:
                        with transaction.atomic():
                            payload = SensitiveMetaPatientRepairCreatePayload(
                                examination_date=date.today(),
                                center_name=_center_name(video),
                            )
                            sensitive_meta = sensitive_meta_factory.create_from_dict(
                                dump_sensitive_meta_patient_repair_create_payload(
                                    payload
                                )
                            )
                            video.sensitive_meta = sensitive_meta
                            video.save(update_fields=["sensitive_meta"])
                            created_count += 1

                    except Exception as e:
                        self.stdout.write(
                            self.style.ERROR(
                                f"Failed to create SensitiveMeta for video {video.video_hash}: {e}"
                            )
                        )
                else:
                    created_count += 1

        # Process videos with incomplete SensitiveMeta
        if count_incomplete > 0:
            self.stdout.write(
                f"\nProcessing {count_incomplete} videos with incomplete SensitiveMeta..."
            )

            for raw_video in videos_with_incomplete_meta:
                video = cast(_RepairVideo, raw_video)
                sensitive_meta = video.sensitive_meta
                if sensitive_meta is None:
                    continue  # Skip if somehow None (already handled above)

                update_data, missing_fields = _missing_patient_update(sensitive_meta)

                if update_data:
                    if verbose:
                        self.stdout.write(
                            f"Updating video {video.video_hash} - missing fields: {', '.join(missing_fields)}"
                        )

                    if not dry_run:
                        try:
                            with transaction.atomic():
                                sensitive_meta.update_from_dict(update_data)
                                fixed_count += 1
                        except Exception as e:
                            self.stdout.write(
                                self.style.ERROR(
                                    f"Failed to update SensitiveMeta for video {video.video_hash}: {e}"
                                )
                            )
                    else:
                        fixed_count += 1

        # Summary
        self.stdout.write("\n" + "=" * 50)
        if dry_run:
            self.stdout.write(self.style.SUCCESS("DRY RUN SUMMARY:"))
            self.stdout.write(f"Would create SensitiveMeta for: {created_count} videos")
            self.stdout.write(f"Would update incomplete data for: {fixed_count} videos")
            self.stdout.write(
                f"Total videos that would be fixed: {created_count + fixed_count}"
            )
        else:
            self.stdout.write(self.style.SUCCESS("REPAIR COMPLETED:"))
            self.stdout.write(f"Created SensitiveMeta for: {created_count} videos")
            self.stdout.write(f"Updated incomplete data for: {fixed_count} videos")
            self.stdout.write(f"Total videos fixed: {created_count + fixed_count}")

        if not dry_run and (created_count > 0 or fixed_count > 0):
            self.stdout.write(
                self.style.SUCCESS("\n✅ Patient data repair completed successfully!")
            )
            self.stdout.write(
                "All videos now have the minimum required patient data for annotation."
            )
