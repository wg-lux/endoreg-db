from django.test import SimpleTestCase

from endoreg_db.models import UploadJob
from endoreg_db.services.hub.ingest import record_active_learning_selection_provenance
from lx_dtypes.models.contracts.hub_active_learning import (
    ActiveLearningSelectionProvenancePayload,
)


class HubIngestActiveLearningProvenanceTests(SimpleTestCase):
    def test_records_active_learning_metadata_without_overwriting_sidecar(self):
        upload_job = UploadJob()
        upload_job.processing_provenance = {
            "sidecar_payload": {
                "existing": "value",
            }
        }

        raw_provenance = record_active_learning_selection_provenance(
            upload_job,
            candidate_count=120,
            selected_count=24,
            annotation_budget=24,
            ai_dataset_id=7,
            model_meta_id=11,
            extra_metadata={"campaign": "round-3"},
            save=False,
        )
        provenance = ActiveLearningSelectionProvenancePayload.model_validate(
            raw_provenance
        )
        active_learning = provenance.sidecar_payload.active_learning

        self.assertEqual(provenance.ingest_variant, "active_learning_selection")
        self.assertEqual(provenance.custom_marker, "active_learning")
        self.assertEqual(provenance.sidecar_payload.existing, "value")
        self.assertEqual(
            active_learning.selection_strategy,
            "temporal_segment_hybrid",
        )
        self.assertEqual(
            active_learning.candidate_count,
            120,
        )
        self.assertEqual(
            active_learning.selected_count,
            24,
        )
        self.assertEqual(
            active_learning.annotation_budget,
            24,
        )
        self.assertEqual(active_learning.campaign, "round-3")
