from __future__ import annotations

from django.core.management.base import BaseCommand

from endoreg_db.services.media_integrity import reconcile_media_integrity


class Command(BaseCommand):
    help = (
        "Reconcile media metadata against on-disk state, repair conservative "
        "discrepancies, and mark unrecoverable records as LOST."
    )

    def handle(self, *args, **options) -> None:
        summary = reconcile_media_integrity()
        self.stdout.write(
            self.style.SUCCESS(
                "media integrity reconciliation complete: "
                f"videos={summary.checked_videos} "
                f"upload_jobs={summary.checked_upload_jobs} "
                f"repaired={summary.repaired_records} "
                f"lost={summary.lost_records}"
            )
        )
