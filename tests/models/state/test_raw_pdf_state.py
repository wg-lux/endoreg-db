from __future__ import annotations

from django.test import TestCase

from endoreg_db.models.state.anonymization import AnonymizationState
from endoreg_db.models.state.raw_pdf import RawPdfState


class TestRawPdfState(TestCase):
    def test_report_import_failure_and_retry_are_visible_in_status(self) -> None:
        state = RawPdfState.objects.create()

        self.assertEqual(state.anonymization_status, AnonymizationState.NOT_STARTED)

        state.mark_processing_started()
        state.refresh_from_db()
        self.assertEqual(
            state.anonymization_status,
            AnonymizationState.PROCESSING_ANONYMIZING,
        )

        state.anonymized = True
        state.sensitive_meta_processed = True
        state.anonymization_validated = True
        state.processed_file_sha256 = "a" * 64
        state.save()
        state.mark_processing_failed()
        state.refresh_from_db()
        self.assertEqual(state.anonymization_status, AnonymizationState.FAILED)
        self.assertFalse(state.processing_started)
        self.assertFalse(state.anonymized)
        self.assertFalse(state.sensitive_meta_processed)
        self.assertFalse(state.anonymization_validated)
        self.assertEqual(state.processed_file_sha256, "")

        state.mark_processing_started()
        state.refresh_from_db()
        self.assertFalse(state.processing_error)
        self.assertEqual(
            state.anonymization_status,
            AnonymizationState.PROCESSING_ANONYMIZING,
        )
