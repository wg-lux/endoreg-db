from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone
from PIL import Image

from endoreg_db.models import (
    AIDataSet,
    Center,
    Frame,
    ImageClassificationAnnotation,
    Label,
    LabelSet,
    VideoFile,
    VideoState,
)
from endoreg_db.services.aidataset_training_manifests import (
    build_frame_multilabel_training_manifest,
)


class AIDataSetTrainingManifestTests(TestCase):
    def setUp(self):
        center = Center.objects.create(name="training-manifest-center")
        self.video = VideoFile.objects.create(
            center=center,
            video_hash="training-manifest-video",
            original_file_name="training_manifest.mp4",
            fps=25.0,
            frame_count=2,
            processed_file="processed/training_manifest.mp4",
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

    def test_extracted_frames_cannot_bypass_clinical_state_gates(self):
        state = self.video.state
        assert state is not None
        for field_name, invalid_value in (
            ("anonymized", False),
            ("anonymization_validated", False),
            ("segment_annotations_validated", False),
            ("outside_segments_removed", False),
            ("ready_for_export", False),
            ("processing_error", True),
        ):
            with self.subTest(field_name=field_name):
                original_value = getattr(state, field_name)
                invalid_state = {field_name: invalid_value, "ready_for_export": False}
                VideoState.objects.filter(pk=state.pk).update(**invalid_state)
                with self.assertRaisesRegex(ValueError, "no extracted or validated"):
                    build_frame_multilabel_training_manifest(
                        self.dataset, label_set=self.label_set, check_frame_format=False
                    )
                VideoState.objects.filter(pk=state.pk).update(
                    **{field_name: original_value, "ready_for_export": True}
                )

    def test_extracted_frames_require_processed_artifact_and_usable_integrity(self):
        for updates in (
            {"processed_file": ""},
            {"state": None},
            {"meta": {"integrity_status": "lost"}},
            {"meta": {"integrity_status": "LOST"}},
        ):
            with self.subTest(updates=updates):
                VideoFile.objects.filter(pk=self.video.pk).update(**updates)
                with self.assertRaisesRegex(ValueError, "no extracted or validated"):
                    build_frame_multilabel_training_manifest(
                        self.dataset, label_set=self.label_set, check_frame_format=False
                    )
                self.video.save()

    def test_build_frame_multilabel_training_manifest_preserves_unknowns(self):
        manifest = build_frame_multilabel_training_manifest(
            self.dataset,
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
        manifest = build_frame_multilabel_training_manifest(
            self.dataset,
            label_set=self.label_set,
            treat_unlabeled_as_negative=True,
            check_frame_format=False,
        )

        assert manifest.samples[1].labels == [0.0, 0.0]
        assert manifest.samples[1].label_mask == [1, 1]
        assert manifest.provenance["treat_unlabeled_as_negative"] is True
        assert manifest.provenance["frame_source_mode"] == (
            "selected_frame_materialization"
        )
        assert manifest.provenance["source_video_kind_by_video_uuid"] == {
            str(self.video.uuid): "processed"
        }
        assert manifest.provenance["frame_ids"] == [
            self.frames[0].pk,
            self.frames[1].pk,
        ]
        assert manifest.provenance["frame_numbers"] == [0, 1]
        assert manifest.provenance["frame_numbers_by_video_uuid"] == {
            str(self.video.uuid): [0, 1]
        }
        assert manifest.provenance["materialization_timestamp"]

    def test_export_rejects_relative_cache_paths_but_preserves_identity_manifest(
        self,
    ) -> None:
        manifest = build_frame_multilabel_training_manifest(
            self.dataset,
            label_set=self.label_set,
            check_frame_format=False,
        )
        assert manifest.samples[0].frame_id == self.frames[0].pk
        assert manifest.samples[0].path is None
        assert manifest.samples[0].metadata["annotation_ids_by_label"]
        with self.assertRaisesRegex(
            ValueError, "protected processed-frame materialization"
        ):
            manifest.to_lx_ai_core_dict()

    def test_absolute_frame_cache_path_export_is_rejected(self) -> None:
        with self.assertRaisesRegex(
            ValueError, "protected processed-frame materialization"
        ):
            build_frame_multilabel_training_manifest(
                self.dataset,
                label_set=self.label_set,
                check_frame_format=False,
                include_file_paths=True,
            )

    def test_build_frame_multilabel_training_manifest_checks_processed_frame_format(
        self,
    ) -> None:
        with patch(
            "endoreg_db.services.frames.training_images.read_processed_training_image",
            side_effect=[Image.new("RGB", (64, 48)), Image.new("RGB", (64, 48))],
        ) as reader:
            manifest = build_frame_multilabel_training_manifest(
                self.dataset, label_set=self.label_set
            )
        assert reader.call_count == 2
        assert [call.args[0].pk for call in reader.call_args_list] == [
            frame.pk for frame in self.frames
        ]
        assert manifest.frame_format.status == "passed"
        assert manifest.frame_format.checked_frame_count == 2
        assert manifest.frame_format.expected_image_format == "JPEG"
        assert manifest.frame_format.expected_width == 64
        assert manifest.frame_format.expected_height == 48
        assert manifest.frame_format.expected_mode == "RGB"

    def test_build_frame_multilabel_training_manifest_rejects_processed_format_mismatch(
        self,
    ) -> None:
        with patch(
            "endoreg_db.services.frames.training_images.read_processed_training_image",
            side_effect=[Image.new("RGB", (64, 48)), Image.new("RGB", (80, 48))],
        ):
            with self.assertRaisesRegex(ValueError, "Frame format validation failed"):
                build_frame_multilabel_training_manifest(
                    self.dataset, label_set=self.label_set
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
            build_frame_multilabel_training_manifest(
                self.dataset,
                label_set=self.label_set,
                check_frame_format=False,
            )
