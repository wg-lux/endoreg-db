"""Operator shortlist integration against persisted scope and installed lx-ai-core."""

import json
import stat
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from django.utils import timezone

from endoreg_db.models import (
    AIDataSet,
    AiModel,
    Center,
    Frame,
    ImageClassificationAnnotation,
    Label,
    LabelSet,
    LabelVideoSegment,
    ModelMeta,
    VideoFile,
    VideoState,
)
from endoreg_db.utils.filesystem.file_operations import atomic_write_file
from endoreg_db.utils.paths import PROTECTED_DATA_ROOT


class ActiveLearningShortlistCommandTests(TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory(dir=PROTECTED_DATA_ROOT)
        self.addCleanup(self.directory.cleanup)
        root = Path(self.directory.name)
        self.candidates_path = root / "candidates.json"
        self.config_path = root / "config.yml"
        self.output_path = root / "shortlist.json"
        self.label = Label.objects.create(name="shortlist-polyp")
        labelset = LabelSet.objects.create(name="shortlist-labelset", version=1)
        labelset.labels.add(self.label)
        self.model = AiModel.objects.create(name="shortlist-model")
        self.model_meta = ModelMeta.objects.create(
            name="shortlist-model-meta",
            version="1",
            model=self.model,
            labelset=labelset,
        )
        self.video = VideoFile.objects.create(
            center=Center.objects.create(name="shortlist-center"),
            video_hash="shortlist-video",
            original_file_name="shortlist.mp4",
            fps=25.0,
            frame_count=200,
            processed_file="processed/shortlist.mp4",
            state=VideoState.objects.create(
                anonymized=True,
                anonymization_validated=True,
                segment_annotations_validated=True,
                outside_segments_removed=True,
                ready_for_export=True,
                ready_for_export_at=timezone.now(),
                ready_for_export_by="test-suite",
                processed_file_sha256="a" * 64,
            ),
        )
        self.frame = Frame.objects.create(
            video=self.video,
            frame_number=100,
            timestamp=4.125,
            relative_path="frame_0000100.jpg",
        )
        self.annotation = ImageClassificationAnnotation.objects.create(
            frame=self.frame, label=self.label, value=True, annotator="reviewer"
        )
        self.dataset = AIDataSet.objects.create(
            name="shortlist-dataset",
            dataset_type=AIDataSet.DATASET_TYPE_IMAGE,
            ai_model_type=AIDataSet.AI_MODEL_TYPE_IMAGE_MULTILABEL,
        )
        self.dataset.image_annotations.add(self.annotation)
        self.candidate = dict(
            sample_index=0,
            frame_id=self.frame.pk,
            video_id=self.video.pk,
            frame_number=100,
            timestamp=4.125,
            probs=[0.5],
            embedding=[1.0, 0.0],
            quality_score=0.9,
        )
        self._write(self.candidates_path, json.dumps([self.candidate]))
        self._write(self.config_path, "budget: 1\n")

    def _write(self, path: Path, text: str) -> None:
        payload = text.encode("utf-8")
        atomic_write_file(
            destination=path,
            content=[payload],
            required_bytes=len(payload),
            file_mode=0o600,
        )

    def _run(self, **overrides: object) -> None:
        options: dict[str, object] = dict(
            candidates_json=self.candidates_path,
            config_yaml=self.config_path,
            output_json=self.output_path,
            dataset_id=self.dataset.pk,
            model_meta_id=self.model_meta.pk,
            stdout=StringIO(),
        )
        options.update(overrides)
        call_command("select_active_learning_candidates", **options)

    def _approve_segment_fixture(self) -> None:
        # Segment writes invalidate approval. Model the separate completed review
        # explicitly so these tests isolate dataset membership, not stale approval.
        state = self.video.state
        assert state is not None
        VideoState.objects.filter(pk=state.pk).update(
            segment_annotations_validated=True,
            outside_segments_removed=True,
            ready_for_export=True,
            ready_for_export_at=timezone.now(),
            ready_for_export_by="test-suite",
            processed_file_sha256="a" * 64,
        )

    def test_shortlist_preserves_provenance_and_never_promotes_predictions(self):
        original_annotations = list(
            ImageClassificationAnnotation.objects.values_list(
                "pk", "value", "annotator"
            )
        )
        self._run()
        payload = json.loads(self.output_path.read_text())
        assert payload["review_status"] == "pending_human_review"
        assert payload["dataset_id"] == self.dataset.pk
        assert payload["model_meta_id"] == self.model_meta.pk
        assert payload["label_ids"] == [self.label.pk]
        assert payload["label_names"] == [self.label.name]
        assert payload["selection"]["selected_frame_ids"] == [self.frame.pk]
        assert payload["selection"]["selected_candidates"][0]["timestamp"] == 4.125
        assert stat.S_IMODE(self.output_path.stat().st_mode) == 0o600
        assert (
            list(
                ImageClassificationAnnotation.objects.values_list(
                    "pk", "value", "annotator"
                )
            )
            == original_annotations
        )
        assert list(self.dataset.image_annotations.values_list("pk", flat=True)) == [
            self.annotation.pk
        ]
        self.model.refresh_from_db()
        assert self.model.active_meta is None

    def test_segment_membership_requires_exact_half_open_frame_coverage(self):
        self.dataset.image_annotations.clear()
        segment = LabelVideoSegment.objects.create(
            video_file=self.video,
            label=self.label,
            start_frame_number=100,
            end_frame_number=101,
        )
        self.dataset.video_annotations.add(segment)
        self._approve_segment_fixture()
        self._run()
        assert self.output_path.exists()

    def test_same_video_frame_outside_attached_segment_is_rejected(self):
        self.dataset.image_annotations.clear()
        segment = LabelVideoSegment.objects.create(
            video_file=self.video,
            label=self.label,
            start_frame_number=99,
            end_frame_number=100,
        )
        self.dataset.video_annotations.add(segment)
        self._approve_segment_fixture()
        with self.assertRaises(CommandError):
            self._run()
        assert not self.output_path.exists()

    def test_forged_identity_and_label_width_are_rejected(self):
        for field, value in (
            ("frame_id", self.frame.pk + 1000),
            ("video_id", self.video.pk + 1000),
            ("frame_number", 99),
            ("timestamp", 4.0),
            ("probs", [0.5, 0.5]),
        ):
            with self.subTest(field=field):
                self._write(
                    self.candidates_path, json.dumps([{**self.candidate, field: value}])
                )
                with self.assertRaises(CommandError):
                    self._run()
                assert not self.output_path.exists()

    def test_unsafe_processed_state_is_rejected_without_reading_raw_media(self):
        VideoState.objects.filter(pk=self.video.state_id).update(ready_for_export=False)
        with self.assertRaises(CommandError):
            self._run()
        assert not self.output_path.exists()

    def test_unattached_candidate_is_rejected(self):
        self.dataset.image_annotations.clear()
        with self.assertRaises(CommandError):
            self._run()
        assert not self.output_path.exists()

    def test_unknown_dataset_and_model_are_rejected(self):
        for field in ("dataset_id", "model_meta_id"):
            with self.subTest(field=field), self.assertRaises(CommandError):
                self._run(**{field: 999999})
        assert not self.output_path.exists()

    def test_unprotected_output_is_rejected(self):
        with TemporaryDirectory() as outside:
            destination = Path(outside) / "shortlist.json"
            with self.assertRaises(CommandError):
                self._run(output_json=destination)
            assert not destination.exists()

    def test_existing_output_is_preserved(self):
        self._write(self.output_path, "existing evidence")
        with self.assertRaises(CommandError):
            self._run()
        assert self.output_path.read_text() == "existing evidence"

    def test_duplicate_keys_nonfinite_and_unknown_candidate_fields_are_rejected(self):
        valid = json.dumps(self.candidate)
        malformed = [
            "["
            + valid.replace('"sample_index": 0', '"sample_index": 0, "sample_index": 1')
            + "]",
            "[" + valid.replace('"timestamp": 4.125', '"timestamp": NaN') + "]",
            json.dumps([{**self.candidate, "unexpected": True}]),
        ]
        for text in malformed:
            with self.subTest(text=text):
                self._write(self.candidates_path, text)
                with self.assertRaises(CommandError):
                    self._run()
                assert not self.output_path.exists()

    def test_invalid_yaml_is_rejected(self):
        for text in (
            "budget: 1\nbudget: 2\n",
            "unknown: 1\n",
            "budget: &budget 1\n",
            "max_label_weight: .inf\n",
        ):
            with self.subTest(text=text):
                self._write(self.config_path, text)
                with self.assertRaises(CommandError):
                    self._run()
                assert not self.output_path.exists()
