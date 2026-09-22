from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone
from PIL import Image

from endoreg_db.models import (
    AIDataSet,
    Center,
    Frame,
    ImageClassificationAnnotation,
    InformationSource,
    Label,
    LabelSet,
    LabelVideoSegment,
    LabelVideoSegmentState,
    VideoFile,
    VideoState,
)
from endoreg_db.services.aidataset_training_manifests import (
    build_frame_multilabel_training_manifest,
)


class AIDataSetTrainingManifestTests(TestCase):
    def setUp(self):
        self.manual_source = InformationSource.objects.create(name="manual_annotation")
        center = Center.objects.create(name="training-manifest-center")
        self.video = VideoFile.objects.create(
            center=center,
            raw_video_hash="training-manifest-video",
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
                information_source=self.manual_source,
                frame=self.frames[0],
                label=self.blood,
                value=False,
                annotator="manifest",
            ),
            ImageClassificationAnnotation.objects.create(
                information_source=self.manual_source,
                frame=self.frames[0],
                label=self.polyp,
                value=True,
                annotator="manifest",
            ),
            ImageClassificationAnnotation.objects.create(
                information_source=self.manual_source,
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

    def test_export_streams_processed_frames_without_cache_paths(
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
        payload = manifest.to_lx_ai_core_dict()
        sample = payload["samples"][0]
        assert "path" not in sample
        assert "relative_path" not in sample["metadata"]
        assert sample["frame_stream"] == {
            "video_id": self.video.pk,
            "frame_number": 0,
            "artifact_kind": "processed",
        }

    def test_absolute_frame_cache_path_export_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "include_file_paths must be false"):
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
            information_source=self.manual_source,
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

    def _add_training_segment(
        self,
        *,
        source: InformationSource | None,
        validated: bool = False,
        start: int = 1,
        end: int = 2,
    ) -> LabelVideoSegment:
        segment = LabelVideoSegment.objects.create(
            video_file=self.video,
            label=self.blood,
            source=source,
            start_frame_number=start,
            end_frame_number=end,
        )
        LabelVideoSegmentState.objects.update_or_create(
            origin=segment, defaults={"is_validated": validated}
        )
        self.dataset.video_annotations.add(segment)
        # Segment changes invalidate the media approval; model the explicit reapproval.
        VideoState.objects.filter(pk=self.video.state.pk).update(
            segment_annotations_validated=True,
            outside_segments_removed=True,
            ready_for_export=True,
            ready_for_export_at=timezone.now(),
            ready_for_export_by="test-suite",
            processed_file_sha256="a" * 64,
        )
        return segment

    def test_manual_and_confirmed_segments_match_training_builder_for_each_scope(self):
        from endoreg_db.utils.ai.multilabel_dataset_builder import (
            build_dataset_for_training,
        )

        prediction = InformationSource.objects.create(name="prediction")
        manual = self._add_training_segment(source=self.manual_source)
        confirmed = self._add_training_segment(source=prediction, validated=True)
        before = ImageClassificationAnnotation.objects.count()
        for scope in ("all", "frame_only", "segment_only"):
            with self.subTest(scope=scope):
                manifest = build_frame_multilabel_training_manifest(
                    self.dataset,
                    label_set=self.label_set,
                    annotation_source_scope=scope,
                    check_frame_format=False,
                )
                training = build_dataset_for_training(
                    self.dataset,
                    labelset=self.label_set,
                    annotation_source_scope=scope,
                )
                assert [sample.frame_id for sample in manifest.samples] == training[
                    "frame_ids"
                ]
                assert [sample.label_mask for sample in manifest.samples] == training[
                    "label_masks"
                ]
                assert [sample.labels for sample in manifest.samples] == [
                    [0.0 if value is None else float(value) for value in vector]
                    for vector in training["label_vectors"]
                ]
                assert manifest.provenance["annotation_source_scope"] == scope
                if scope != "frame_only":
                    sample = manifest.samples[-1]
                    assert sample.metadata["segment_ids_by_label"] == {
                        "blood": [manual.pk, confirmed.pk]
                    }
                    assert sample.metadata["annotation_ids_by_label"]["blood"] == []
                    assert sample.timestamp == self.frames[1].timestamp
        assert ImageClassificationAnnotation.objects.count() == before

    def test_unconfirmed_predictions_and_unknown_sources_never_supply_training_labels(
        self,
    ):
        from endoreg_db.utils.ai.multilabel_dataset_builder import (
            build_dataset_for_training,
        )

        prediction = InformationSource.objects.create(name="prediction")
        self._add_training_segment(source=prediction, start=0, end=2)
        self._add_training_segment(source=None, start=0, end=2)
        # These false positives would conflict with the human negative if selected.
        for source in (prediction, None):
            annotation = ImageClassificationAnnotation.objects.create(
                frame=self.frames[0],
                label=self.blood,
                value=True,
                information_source=source,
                annotator="unreviewed",
            )
            self.dataset.image_annotations.add(annotation)
        manifest = build_frame_multilabel_training_manifest(
            self.dataset,
            label_set=self.label_set,
            check_frame_format=False,
        )
        training = build_dataset_for_training(self.dataset, labelset=self.label_set)
        assert manifest.samples[0].labels == [0.0, 1.0]
        assert training["label_vectors"] == [[0, 1], [None, 0]]
        self.dataset.image_annotations.clear()
        for builder in (
            lambda: build_frame_multilabel_training_manifest(
                self.dataset, label_set=self.label_set, check_frame_format=False
            ),
            lambda: build_dataset_for_training(self.dataset, labelset=self.label_set),
        ):
            with self.assertRaises(ValueError):
                builder()

    def test_segment_scope_infers_label_set_and_honors_source_filter(self):
        prediction = InformationSource.objects.create(name="prediction")
        self._add_training_segment(source=prediction, validated=True)
        manifest = build_frame_multilabel_training_manifest(
            self.dataset,
            annotation_source_scope="segment_only",
            check_frame_format=False,
            information_source_names=["prediction"],
        )
        assert len(manifest.samples) == 1
        assert manifest.samples[0].frame_id == self.frames[1].pk
        with self.assertRaises(ValueError):
            build_frame_multilabel_training_manifest(
                self.dataset,
                annotation_source_scope="segment_only",
                check_frame_format=False,
                information_source_names=["manual_annotation"],
            )

    def test_segment_expansion_excludes_end_and_other_dataset_segments(self):
        from endoreg_db.utils.ai.multilabel_dataset_builder import (
            build_dataset_for_training,
        )

        self._add_training_segment(source=self.manual_source, start=0, end=1)
        other = self._add_training_segment(source=self.manual_source, start=1, end=2)
        self.dataset.video_annotations.remove(other)
        manifest = build_frame_multilabel_training_manifest(
            self.dataset,
            label_set=self.label_set,
            annotation_source_scope="segment_only",
            check_frame_format=False,
        )
        training = build_dataset_for_training(
            self.dataset,
            labelset=self.label_set,
            annotation_source_scope="segment_only",
        )
        assert [sample.frame_id for sample in manifest.samples] == [self.frames[0].pk]
        assert training["frame_ids"] == [self.frames[0].pk]
        with self.assertRaisesRegex(ValueError, "Conflicting"):
            build_frame_multilabel_training_manifest(
                self.dataset,
                label_set=self.label_set,
                check_frame_format=False,
            )
        with self.assertRaisesRegex(ValueError, "Conflicting"):
            build_dataset_for_training(self.dataset, labelset=self.label_set)

    def test_confirmed_segment_cannot_bypass_media_approval(self):
        self._add_training_segment(source=self.manual_source, validated=True)
        VideoState.objects.filter(pk=self.video.state.pk).update(ready_for_export=False)
        with self.assertRaisesRegex(ValueError, "no extracted or validated"):
            build_frame_multilabel_training_manifest(
                self.dataset,
                label_set=self.label_set,
                annotation_source_scope="segment_only",
                check_frame_format=False,
            )

    def test_materialized_segment_rows_cannot_bypass_segment_selection(self):
        from endoreg_db.utils.ai.multilabel_dataset_builder import (
            build_dataset_for_training,
        )

        derived = ImageClassificationAnnotation.objects.create(
            frame=self.frames[0],
            label=self.blood,
            value=True,
            information_source=self.manual_source,
            annotator="segment-expansion",
            external_annotation_id="segment-derived:v1:999:1:unreviewed",
        )
        self.dataset.image_annotations.add(derived)
        manifest = build_frame_multilabel_training_manifest(
            self.dataset,
            label_set=self.label_set,
            check_frame_format=False,
        )
        training = build_dataset_for_training(self.dataset, labelset=self.label_set)
        assert manifest.samples[0].labels == [0.0, 1.0]
        assert training["label_vectors"][0] == [0, 1]

    def test_revoked_segment_confirmation_is_excluded_on_next_build(self):
        prediction = InformationSource.objects.create(name="prediction")
        segment = self._add_training_segment(source=prediction, validated=True)
        LabelVideoSegmentState.objects.filter(origin=segment).update(is_validated=False)
        with self.assertRaises(ValueError):
            build_frame_multilabel_training_manifest(
                self.dataset,
                annotation_source_scope="segment_only",
                check_frame_format=False,
            )
