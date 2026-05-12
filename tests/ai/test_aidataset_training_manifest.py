from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory

from django.test import TestCase
from PIL import Image
import pytest

from endoreg_db.models import (
    AIDataSet,
    Center,
    Frame,
    ImageClassificationAnnotation,
    Label,
    LabelSet,
    VideoFile,
)
from endoreg_db.utils.file_operations import atomic_write_file


class AIDataSetTrainingManifestTests(TestCase):
    def setUp(self):
        center = Center.objects.create(name="training-manifest-center")
        self.video = VideoFile.objects.create(
            center=center,
            video_hash="training-manifest-video",
            original_file_name="training_manifest.mp4",
            fps=25.0,
            frame_count=2,
        )
        self.frames = [
            Frame.objects.create(
                video=self.video,
                frame_number=frame_number,
                relative_path=f"frame_{frame_number:07d}.jpg",
                is_extracted=True,
                timestamp=float(frame_number) / 25.0,
            )
            for frame_number in range(2)
        ]
        self.blood = Label.objects.create(name="blood")
        self.polyp = Label.objects.create(name="polyp")
        self.label_set = LabelSet.objects.create(
            name="training-manifest-label-set",
            version=1,
        )
        self.label_set.labels.add(self.polyp, self.blood)
        self.dataset = AIDataSet.objects.create(
            name="training-manifest-dataset",
            dataset_type=AIDataSet.DATASET_TYPE_IMAGE,
            ai_model_type=AIDataSet.AI_MODEL_TYPE_IMAGE_MULTILABEL,
        )
        annotations = [
            ImageClassificationAnnotation.objects.create(
                frame=self.frames[0],
                label=self.blood,
                value=False,
                annotator="manifest",
            ),
            ImageClassificationAnnotation.objects.create(
                frame=self.frames[0],
                label=self.polyp,
                value=True,
                annotator="manifest",
            ),
            ImageClassificationAnnotation.objects.create(
                frame=self.frames[1],
                label=self.polyp,
                value=False,
                annotator="manifest",
            ),
        ]
        self.dataset.image_annotations.add(*annotations)

    def _write_frame_image(
        self,
        frame: Frame,
        frame_dir: str,
        *,
        size: tuple[int, int] = (64, 48),
        image_format: str = "JPEG",
    ) -> None:
        buffer = BytesIO()
        Image.new("RGB", size, color=(0, 0, 0)).save(buffer, format=image_format)
        payload = buffer.getvalue()
        atomic_write_file(
            destination=Path(frame_dir) / frame.relative_path,
            content=[payload],
            required_bytes=len(payload),
        )

    def test_build_frame_multilabel_training_manifest_preserves_unknowns(self):
        manifest = self.dataset.build_frame_multilabel_training_manifest(
            label_set=self.label_set,
            treat_unlabeled_as_negative=False,
            check_frame_format=False,
        )

        assert [label.name for label in manifest.labels] == ["blood", "polyp"]
        assert manifest.class_frequencies == [0.0, 0.5]
        assert len(manifest.samples) == 2

        first_sample = manifest.samples[0]
        assert first_sample.path is None
        assert first_sample.relative_path == "frame_0000000.jpg"
        assert first_sample.labels == [0.0, 1.0]
        assert first_sample.label_mask == [1, 1]
        assert first_sample.group_id == str(self.video.uuid)
        assert first_sample.video_uuid == str(self.video.uuid)

        second_sample = manifest.samples[1]
        assert second_sample.labels == [0.0, 0.0]
        assert second_sample.label_mask == [0, 1]

    def test_build_frame_multilabel_training_manifest_can_mark_unknowns_negative(self):
        manifest = self.dataset.build_frame_multilabel_training_manifest(
            label_set=self.label_set,
            treat_unlabeled_as_negative=True,
            check_frame_format=False,
        )

        assert manifest.samples[1].labels == [0.0, 0.0]
        assert manifest.samples[1].label_mask == [1, 1]
        assert manifest.provenance["treat_unlabeled_as_negative"] is True

    def test_export_lx_ai_core_training_manifest_uses_relative_path_by_default(self):
        payload = self.dataset.export_lx_ai_core_training_manifest(
            label_set=self.label_set,
            check_frame_format=False,
        )

        assert payload["modality"] == "frame"
        assert payload["task_kind"] == "multilabel_classification"
        assert payload["labels"] == ["blood", "polyp"]
        assert payload["samples"][0]["path"] == "frame_0000000.jpg"
        assert payload["samples"][0]["metadata"]["relative_path"] == (
            "frame_0000000.jpg"
        )
        assert payload["samples"][0]["metadata"]["video_uuid"] == str(self.video.uuid)
        assert payload["provenance"]["frame_format"]["status"] == "not_checked"
        assert (
            payload["provenance"]["frame_format"]["preprocessing_strategy"]
            == "preserve_dimensions_black_mask"
        )
        assert (
            payload["provenance"]["frame_format"]["recommended_model_input_strategy"]
            == "crop_to_endoscope_roi"
        )

    def test_export_lx_ai_core_training_manifest_matches_installed_contract(self):
        pytest.importorskip("lx_ai_core.training")
        from lx_ai_core.training import TrainingDatasetManifest

        payload = self.dataset.export_lx_ai_core_training_manifest(
            label_set=self.label_set,
            check_frame_format=False,
        )

        manifest = TrainingDatasetManifest.model_validate(payload)
        assert manifest.dataset_id == self.dataset.pk
        assert manifest.labels == ["blood", "polyp"]

    def test_build_frame_multilabel_training_manifest_checks_frame_format(self):
        with TemporaryDirectory() as frame_dir:
            self.video.frame_dir = frame_dir
            self.video.save(update_fields=["frame_dir"])
            for frame in self.frames:
                self._write_frame_image(frame, frame_dir)

            manifest = self.dataset.build_frame_multilabel_training_manifest(
                label_set=self.label_set,
            )

        assert manifest.frame_format.status == "passed"
        assert manifest.frame_format.checked_frame_count == 2
        assert manifest.frame_format.expected_image_format == "JPEG"
        assert manifest.frame_format.expected_width == 64
        assert manifest.frame_format.expected_height == 48
        assert manifest.frame_format.expected_mode == "RGB"

    def test_build_frame_multilabel_training_manifest_rejects_format_mismatch(self):
        with TemporaryDirectory() as frame_dir:
            self.video.frame_dir = frame_dir
            self.video.save(update_fields=["frame_dir"])
            self._write_frame_image(self.frames[0], frame_dir, size=(64, 48))
            self._write_frame_image(self.frames[1], frame_dir, size=(80, 48))

            with self.assertRaisesRegex(ValueError, "Frame format validation failed"):
                self.dataset.build_frame_multilabel_training_manifest(
                    label_set=self.label_set,
                )

    def test_build_frame_multilabel_training_manifest_rejects_conflicts(self):
        conflict = ImageClassificationAnnotation.objects.create(
            frame=self.frames[0],
            label=self.polyp,
            value=False,
            annotator="conflict",
        )
        self.dataset.image_annotations.add(conflict)

        with self.assertRaisesRegex(ValueError, "Conflicting annotations"):
            self.dataset.build_frame_multilabel_training_manifest(
                label_set=self.label_set,
                check_frame_format=False,
            )
