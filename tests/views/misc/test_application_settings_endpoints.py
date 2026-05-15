from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase
from django.test.utils import override_settings
from django.utils import timezone

from endoreg_db.models import (
    AIDataSet,
    AIDataSetExportArtifact,
    AIModelTrainingRun,
    Center,
    EndoscopyProcessor,
    LabelSet,
    NetworkNode,
)
from endoreg_db.services import model_training_jobs
from endoreg_db.views.misc import application_settings as view_module


class ApplicationSettingsEndpointTests(TestCase):
    def setUp(self):
        suffix = uuid4().hex[:8]
        self.center = Center.objects.create(name=f"settings-center-{suffix}")
        self.processor = EndoscopyProcessor.objects.create(
            name=f"settings-processor-{suffix}",
            image_width=1920,
            image_height=1080,
            endoscope_image_x=0,
            endoscope_image_y=0,
            endoscope_image_width=0,
            endoscope_image_height=0,
            examination_date_x=0,
            examination_date_y=0,
            examination_date_width=0,
            examination_date_height=0,
            patient_first_name_x=0,
            patient_first_name_y=0,
            patient_first_name_width=0,
            patient_first_name_height=0,
            patient_last_name_x=0,
            patient_last_name_y=0,
            patient_last_name_width=0,
            patient_last_name_height=0,
            patient_dob_x=0,
            patient_dob_y=0,
            patient_dob_width=0,
            patient_dob_height=0,
        )

    def test_get_application_settings(self):
        response = self.client.get("/api/settings/application/")
        assert response.status_code == 200, response.content

        payload = response.json()
        assert set(payload.keys()) >= {
            "id",
            "center_id",
            "center_name",
            "processor_id",
            "processor_name",
            "annotator_name",
            "report_template_name",
            "ai_dataset_name",
            "ai_dataset_type",
            "updated_at",
            "backup_status",
        }
        assert set(payload["backup_status"].keys()) >= {
            "ready",
            "missing_paths",
            "required_path_count",
            "available_path_count",
            "source_roots",
        }

    def test_patch_application_settings_with_valid_ids(self):
        response = self.client.patch(
            "/api/settings/application/",
            data={
                "center_id": self.center.pk,
                "processor_id": self.processor.pk,
                "annotator_name": "annotator_a",
                "report_template_name": "template_a",
                "ai_dataset_name": "dataset_a",
                "ai_dataset_type": "image",
            },
            content_type="application/json",
        )
        assert response.status_code == 200, response.content
        payload = response.json()
        assert payload["center_id"] == self.center.pk
        assert payload["processor_id"] == self.processor.pk
        assert payload["annotator_name"] == "annotator_a"
        assert payload["report_template_name"] == "template_a"
        assert payload["ai_dataset_name"] == "dataset_a"
        assert payload["ai_dataset_type"] == "image"

    def test_get_application_settings_uses_authenticated_username_as_fallback(self):
        user_model = get_user_model()
        user = user_model.objects.create_user(username="keycloak_user")
        self.client.force_login(user)

        response = self.client.get("/api/settings/application/")

        assert response.status_code == 200, response.content
        payload = response.json()
        assert payload["annotator_name"] == "keycloak_user"

    def test_patch_application_settings_rejects_unknown_center(self):
        response = self.client.patch(
            "/api/settings/application/",
            data={"center_id": 999999},
            content_type="application/json",
        )
        assert response.status_code == 400, response.content
        assert "center" in response.json()["errors"]

    def test_patch_application_settings_rejects_invalid_scalar_types(self):
        response = self.client.patch(
            "/api/settings/application/",
            data={
                "annotator_name": 123,
                "report_template_name": ["template"],
                "ai_dataset_name": {"name": "dataset"},
                "ai_dataset_type": "invalid",
            },
            content_type="application/json",
        )

        assert response.status_code == 400, response.content
        errors = response.json()["errors"]
        assert errors["annotator_name"] == "annotator_name must be a string."
        assert errors["report_template_name"] == (
            "report_template_name must be a string."
        )
        assert errors["ai_dataset_name"] == "ai_dataset_name must be a string."
        assert errors["ai_dataset_type"] == (
            "ai_dataset_type must be one of: image, video."
        )

    def test_application_settings_dropdown_endpoints(self):
        centers_response = self.client.get(
            "/api/settings/application/dropdowns/centers/"
        )
        assert centers_response.status_code == 200, centers_response.content
        assert any(
            entry["id"] == self.center.pk and entry["name"] == self.center.name
            for entry in centers_response.json()
        )

        processors_response = self.client.get(
            "/api/settings/application/dropdowns/processors/"
        )
        assert processors_response.status_code == 200, processors_response.content
        assert any(
            entry["id"] == self.processor.pk and entry["name"] == self.processor.name
            for entry in processors_response.json()
        )

        annotators_response = self.client.get(
            "/api/settings/application/dropdowns/annotators/"
        )
        assert annotators_response.status_code == 200, annotators_response.content
        assert isinstance(annotators_response.json(), list)

        templates_response = self.client.get(
            "/api/settings/application/dropdowns/report_templates/"
        )
        assert templates_response.status_code == 200, templates_response.content
        assert isinstance(templates_response.json(), list)

        datasets_response = self.client.get(
            "/api/settings/application/dropdowns/ai_datasets/"
        )
        assert datasets_response.status_code == 200, datasets_response.content
        assert isinstance(datasets_response.json(), list)

    def test_ai_dataset_export_endpoint_exports_selected_dataset(self):
        dataset = AIDataSet.objects.create(
            name=f"dataset-export-{uuid4().hex[:8]}",
            dataset_type=AIDataSet.DATASET_TYPE_IMAGE,
            ai_model_type=AIDataSet.AI_MODEL_TYPE_IMAGE_MULTILABEL,
        )

        response = self.client.post(
            "/api/settings/application/ai_dataset_export/",
            data={
                "dataset_id": dataset.pk,
            },
            content_type="application/json",
        )
        assert response.status_code == 201, response.content
        payload = response.json()
        assert payload["success"] is True
        assert payload["artifact_id"]
        assert payload["dataset_id"] == dataset.pk
        assert payload["download_url"].endswith(f"/{payload['artifact_id']}/download/")
        assert payload["sha256"]
        assert payload["byte_size"] > 0
        output_path = Path(payload["output_path"])
        assert output_path.exists()
        exported = output_path.read_text(encoding="utf-8")
        assert dataset.name in exported
        artifact = AIDataSetExportArtifact.objects.get(
            artifact_id=payload["artifact_id"]
        )
        assert artifact.status == AIDataSetExportArtifact.STATUS_COMPLETED

        download_response = self.client.get(payload["download_url"])
        assert download_response.status_code == 200, download_response.content
        assert download_response["X-Content-SHA256"] == payload["sha256"]

    def test_ai_dataset_dropdown_includes_existing_datasets(self):
        dataset = AIDataSet.objects.create(
            name=f"dataset-dropdown-{uuid4().hex[:8]}",
            dataset_type=AIDataSet.DATASET_TYPE_VIDEO,
            ai_model_type=AIDataSet.AI_MODEL_TYPE_VIDEO_SEGMENT_CLASSIFICATION,
        )

        response = self.client.get("/api/settings/application/dropdowns/ai_datasets/")
        assert response.status_code == 200, response.content
        assert any(
            entry["id"] == dataset.pk
            and entry["value"] == dataset.name
            and entry["dataset_type"] == dataset.dataset_type
            for entry in response.json()
        )

    def test_ai_dataset_dropdown_post_returns_current_duplicate_name_count(self):
        dataset_name = f"dataset-duplicate-{uuid4().hex[:8]}"
        AIDataSet.objects.create(
            name=dataset_name,
            dataset_type=AIDataSet.DATASET_TYPE_IMAGE,
            ai_model_type=AIDataSet.AI_MODEL_TYPE_IMAGE_MULTILABEL,
        )

        response = self.client.post(
            "/api/settings/application/dropdowns/ai_datasets/",
            data={
                "name": dataset_name,
                "dataset_type": AIDataSet.DATASET_TYPE_IMAGE,
            },
            content_type="application/json",
        )

        assert response.status_code == 201, response.content
        payload = response.json()
        assert payload["value"] == dataset_name
        assert payload["dataset_type"] == AIDataSet.DATASET_TYPE_IMAGE
        assert payload["ai_model_type"] == AIDataSet.AI_MODEL_TYPE_IMAGE_MULTILABEL
        assert payload["name_count"] == 2

    def test_ai_dataset_frame_bucket_distribution_endpoint(self):
        dataset = AIDataSet.objects.create(
            name=f"dataset-buckets-{uuid4().hex[:8]}",
            dataset_type=AIDataSet.DATASET_TYPE_IMAGE,
            ai_model_type=AIDataSet.AI_MODEL_TYPE_IMAGE_MULTILABEL,
        )

        response = self.client.get(
            f"/api/settings/application/ai_datasets/{dataset.pk}/frame_bucket_distribution/",
            {"prediction_segments_only": "false"},
        )

        assert response.status_code == 200, response.content
        payload = response.json()
        assert payload["dataset_id"] == dataset.pk
        assert payload["prediction_segments_only"] is False
        assert payload["target_buckets"] == [
            {"bucket": "positive", "frame_count": 0},
            {"bucket": "negative", "frame_count": 0},
            {"bucket": "unknown", "frame_count": 0},
        ]
        assert payload["summary"]["merged_frame_count"] == 0

    def test_ai_dataset_frame_bucket_distribution_endpoint_accepts_name_param(self):
        dataset = AIDataSet.objects.create(
            name=f"dataset-buckets-name-{uuid4().hex[:8]}",
            dataset_type=AIDataSet.DATASET_TYPE_IMAGE,
            ai_model_type=AIDataSet.AI_MODEL_TYPE_IMAGE_MULTILABEL,
        )

        response = self.client.get(
            f"/api/settings/application/ai_datasets/{dataset.name}/frame_bucket_distribution/",
            {"prediction_segments_only": "false"},
        )

        assert response.status_code == 200, response.content
        payload = response.json()
        assert payload["dataset_id"] == dataset.pk

    def test_ai_dataset_training_manifest_endpoint_passes_config(self):
        dataset = AIDataSet.objects.create(
            name=f"dataset-manifest-{uuid4().hex[:8]}",
            dataset_type=AIDataSet.DATASET_TYPE_IMAGE,
            ai_model_type=AIDataSet.AI_MODEL_TYPE_IMAGE_MULTILABEL,
        )
        label_set = LabelSet.objects.create(
            name=f"manifest-label-set-{uuid4().hex[:8]}",
            version=1,
        )

        class StubFrameFormat:
            def model_dump(self, **kwargs):
                return {
                    "status": "not_checked",
                    "preprocessing_strategy": "crop_to_endoscope_roi",
                }

        class StubManifest:
            labels = [object(), object()]
            samples = [object()]
            class_frequencies = [0.0, 1.0]
            frame_format = StubFrameFormat()

            def model_dump(self, **kwargs):
                return {"schema_version": "1.0", "labels": ["a", "b"]}

            def to_lx_ai_core_dict(self):
                return {"schema_version": "1.0", "labels": ["a", "b"]}

        with patch.object(
            AIDataSet,
            "build_frame_multilabel_training_manifest",
            return_value=StubManifest(),
        ) as builder:
            response = self.client.post(
                f"/api/settings/application/ai_datasets/{dataset.pk}/training_manifest/",
                data={
                    "label_set_id": label_set.pk,
                    "treat_unlabeled_as_negative": True,
                    "include_file_paths": False,
                    "check_frame_format": False,
                    "preprocessing_strategy": "crop_to_endoscope_roi",
                    "recommended_model_input_strategy": "crop_to_endoscope_roi",
                    "information_source_names": ["manual_annotation"],
                },
                content_type="application/json",
            )

        assert response.status_code == 200, response.content
        builder.assert_called_once()
        assert builder.call_args.kwargs == {
            "label_set": label_set,
            "treat_unlabeled_as_negative": True,
            "include_file_paths": False,
            "check_frame_format": False,
            "preprocessing_strategy": "crop_to_endoscope_roi",
            "recommended_model_input_strategy": "crop_to_endoscope_roi",
            "information_source_names": ["manual_annotation"],
        }
        payload = response.json()
        assert payload["dataset_id"] == dataset.pk
        assert payload["summary"]["sample_count"] == 1
        assert payload["config"]["label_set_id"] == label_set.pk

    def test_ai_dataset_training_manifest_endpoint_rejects_invalid_strategy(self):
        dataset = AIDataSet.objects.create(
            name=f"dataset-manifest-invalid-{uuid4().hex[:8]}",
            dataset_type=AIDataSet.DATASET_TYPE_IMAGE,
            ai_model_type=AIDataSet.AI_MODEL_TYPE_IMAGE_MULTILABEL,
        )

        response = self.client.post(
            f"/api/settings/application/ai_datasets/{dataset.pk}/training_manifest/",
            data={"preprocessing_strategy": "black_box"},
            content_type="application/json",
        )

        assert response.status_code == 400, response.content
        assert "preprocessing_strategy" in response.json()["errors"]

    def test_ai_dataset_export_rejects_missing_dataset_selection(self):
        response = self.client.post(
            "/api/settings/application/ai_dataset_export/",
            data={"ai_dataset_name": "", "ai_dataset_type": "unknown"},
            content_type="application/json",
        )

        assert response.status_code == 400, response.content
        errors = response.json()["errors"]
        assert "ai_dataset_name" in errors
        assert "ai_dataset_type" in errors

    def test_ai_dataset_export_rejects_ambiguous_name_type_fallback(self):
        dataset_name = f"dataset-duplicate-{uuid4().hex[:8]}"
        for _ in range(2):
            AIDataSet.objects.create(
                name=dataset_name,
                dataset_type=AIDataSet.DATASET_TYPE_IMAGE,
                ai_model_type=AIDataSet.AI_MODEL_TYPE_IMAGE_MULTILABEL,
            )

        response = self.client.post(
            "/api/settings/application/ai_dataset_export/",
            data={
                "ai_dataset_name": dataset_name,
                "ai_dataset_type": AIDataSet.DATASET_TYPE_IMAGE,
            },
            content_type="application/json",
        )

        assert response.status_code == 409, response.content
        assert "Multiple AIDataSet" in response.json()["errors"]["ai_dataset_name"]

    @override_settings(ENDOREG_DEPLOYMENT_ROLE="local_study_server")
    def test_dataset_export_rejects_unprivileged_all_centers_scope(self):
        user_model = get_user_model()
        user = user_model.objects.create_user(username="dataset-scope-user")
        self.client.force_login(user)
        dataset = AIDataSet.objects.create(
            name=f"dataset-scope-{uuid4().hex[:8]}",
            dataset_type=AIDataSet.DATASET_TYPE_IMAGE,
            ai_model_type=AIDataSet.AI_MODEL_TYPE_IMAGE_MULTILABEL,
        )

        response = self.client.post(
            "/api/settings/application/ai_dataset_export/",
            data={
                "ai_dataset_name": dataset.name,
                "ai_dataset_type": dataset.dataset_type,
                "all_centers": True,
            },
            content_type="application/json",
        )

        assert response.status_code == 403, response.content
        assert "all_centers" in response.json()["error"]

    @override_settings(ENDOREG_DEPLOYMENT_ROLE="local_study_server")
    def test_dataset_export_passes_resolved_scope_to_standard_export(self):
        user_model = get_user_model()
        user = user_model.objects.create_user(
            username="dataset-scope-staff",
            is_staff=True,
        )
        self.client.force_login(user)
        dataset = AIDataSet.objects.create(
            name=f"dataset-scoped-export-{uuid4().hex[:8]}",
            dataset_type=AIDataSet.DATASET_TYPE_IMAGE,
            ai_model_type=AIDataSet.AI_MODEL_TYPE_IMAGE_MULTILABEL,
        )

        with patch.object(
            AIDataSet,
            "export_to_standardized_structure",
            return_value={"summary": {}},
        ) as exporter:
            response = self.client.post(
                "/api/settings/application/ai_dataset_export/",
                data={
                    "ai_dataset_name": dataset.name,
                    "ai_dataset_type": dataset.dataset_type,
                    "center_key": self.center.center_key,
                    "only_validated": "true",
                    "all_centers": "false",
                },
                content_type="application/json",
            )

        assert response.status_code == 201, response.content
        assert exporter.call_args.kwargs == {
            "center_key": self.center.center_key,
            "all_centers": False,
            "only_validated": True,
        }

    def test_model_training_options_endpoint_returns_backbones_and_image_datasets(self):
        image_dataset = AIDataSet.objects.create(
            name=f"train-image-{uuid4().hex[:8]}",
            dataset_type=AIDataSet.DATASET_TYPE_IMAGE,
            ai_model_type=AIDataSet.AI_MODEL_TYPE_IMAGE_MULTILABEL,
        )
        AIDataSet.objects.create(
            name=f"train-video-{uuid4().hex[:8]}",
            dataset_type=AIDataSet.DATASET_TYPE_VIDEO,
            ai_model_type=AIDataSet.AI_MODEL_TYPE_VIDEO_SEGMENT_CLASSIFICATION,
        )

        response = self.client.get("/api/settings/application/model_training/options/")

        assert response.status_code == 200, response.content
        payload = response.json()
        assert any(entry["id"] == image_dataset.pk for entry in payload["ai_datasets"])
        assert any(
            option["value"] == "phi_region_detector"
            for option in payload["training_targets"]
        )
        assert payload["phi_region_detector"]["defaults"]["input_size"] == 640
        assert any(option["value"] == "gastro_rn50" for option in payload["backbones"])
        assert any(
            option["value"] == "freeze_backbone" for option in payload["feature_modes"]
        )

    def test_model_training_run_endpoints_create_and_report_run(self):
        dataset = AIDataSet.objects.create(
            name=f"train-run-{uuid4().hex[:8]}",
            dataset_type=AIDataSet.DATASET_TYPE_IMAGE,
            ai_model_type=AIDataSet.AI_MODEL_TYPE_IMAGE_MULTILABEL,
        )

        from endoreg_db.views.misc import application_settings as view_module

        captured_kwargs: dict[str, object] = {}

        def fake_launch(run_id: str, *, command_kwargs: dict[str, object]) -> None:
            captured_kwargs.update(command_kwargs)
            run = AIModelTrainingRun.objects.get(run_id=run_id)
            run.status = AIModelTrainingRun.STATUS_COMPLETED
            run.started_at = timezone.now()
            run.finished_at = timezone.now()
            run.stdout = 'training finished\n{"model_path": "/tmp/model.pth", "meta_path": "/tmp/meta.json"}'
            run.result = {
                "model_path": "/tmp/model.pth",
                "meta_path": "/tmp/meta.json",
            }
            run.artifact_paths = {
                "model_path": "/tmp/model.pth",
                "meta_path": "/tmp/meta.json",
            }
            run.error = ""
            run.save(
                update_fields=[
                    "status",
                    "started_at",
                    "finished_at",
                    "stdout",
                    "result",
                    "artifact_paths",
                    "error",
                    "updated_at",
                ]
            )

        original_launch = view_module._launch_model_training_run
        try:
            view_module._launch_model_training_run = fake_launch
            create_response = self.client.post(
                "/api/settings/application/model_training/runs/",
                data={
                    "dataset_id": dataset.pk,
                    "backbone_name": "resnet50_imagenet",
                    "feature_mode": "fine_tune_backbone",
                    "epochs": 3,
                    "batch_size": 8,
                    "labelset_version": 2,
                    "device": "cpu",
                    "annotation_source_scope": "segment_only",
                    "treat_unlabeled_as_negative": False,
                },
                content_type="application/json",
            )
        finally:
            view_module._launch_model_training_run = original_launch

        assert create_response.status_code == 202, create_response.content
        created_payload = create_response.json()
        assert created_payload["dataset_id"] == dataset.pk
        assert created_payload["backbone_name"] == "resnet50_imagenet"
        assert created_payload["feature_mode"] == "fine_tune_backbone"
        assert created_payload["freeze_backbone"] is False
        assert created_payload["annotation_source_scope"] == "segment_only"
        assert captured_kwargs["device"] == "cpu"
        assert captured_kwargs["annotation_source_scope"] == "segment_only"
        assert AIModelTrainingRun.objects.filter(
            run_id=created_payload["run_id"]
        ).exists()

        detail_response = self.client.get(
            f"/api/settings/application/model_training/runs/{created_payload['run_id']}/"
        )
        assert detail_response.status_code == 200, detail_response.content
        detail_payload = detail_response.json()
        assert detail_payload["status"] == "completed"
        assert detail_payload["annotation_source_scope"] == "segment_only"
        assert detail_payload["result"]["model_path"] == "/tmp/model.pth"
        assert detail_payload["artifact_paths"]["meta_path"] == "/tmp/meta.json"
        assert "training finished" in detail_payload["stdout"]

        list_response = self.client.get(
            "/api/settings/application/model_training/runs/"
        )
        assert list_response.status_code == 200, list_response.content
        listed_payload = next(
            entry
            for entry in list_response.json()
            if entry["run_id"] == created_payload["run_id"]
        )
        assert listed_payload["annotation_source_scope"] == "segment_only"

    def test_model_training_run_endpoint_defaults_annotation_source_scope_to_all(
        self,
    ):
        dataset = AIDataSet.objects.create(
            name=f"train-run-default-scope-{uuid4().hex[:8]}",
            dataset_type=AIDataSet.DATASET_TYPE_IMAGE,
            ai_model_type=AIDataSet.AI_MODEL_TYPE_IMAGE_MULTILABEL,
        )

        from endoreg_db.views.misc import application_settings as view_module

        captured_kwargs: dict[str, object] = {}

        def fake_launch(run_id: str, *, command_kwargs: dict[str, object]) -> None:
            captured_kwargs.update(command_kwargs)

        original_launch = view_module._launch_model_training_run
        try:
            view_module._launch_model_training_run = fake_launch
            create_response = self.client.post(
                "/api/settings/application/model_training/runs/",
                data={
                    "dataset_id": dataset.pk,
                    "backbone_name": "resnet50_imagenet",
                    "feature_mode": "freeze_backbone",
                    "epochs": 3,
                    "batch_size": 8,
                    "labelset_version": 2,
                },
                content_type="application/json",
            )
        finally:
            view_module._launch_model_training_run = original_launch

        assert create_response.status_code == 202, create_response.content
        created_payload = create_response.json()
        assert created_payload["annotation_source_scope"] == "all"
        assert captured_kwargs["annotation_source_scope"] == "all"

        detail_response = self.client.get(
            f"/api/settings/application/model_training/runs/{created_payload['run_id']}/"
        )
        assert detail_response.status_code == 200, detail_response.content
        assert detail_response.json()["annotation_source_scope"] == "all"

    def test_model_training_run_endpoint_rejects_invalid_annotation_source_scope(
        self,
    ):
        dataset = AIDataSet.objects.create(
            name=f"train-run-invalid-scope-{uuid4().hex[:8]}",
            dataset_type=AIDataSet.DATASET_TYPE_IMAGE,
            ai_model_type=AIDataSet.AI_MODEL_TYPE_IMAGE_MULTILABEL,
        )

        response = self.client.post(
            "/api/settings/application/model_training/runs/",
            data={
                "dataset_id": dataset.pk,
                "backbone_name": "resnet50_imagenet",
                "feature_mode": "fine_tune_backbone",
                "epochs": 3,
                "batch_size": 8,
                "labelset_version": 2,
                "annotation_source_scope": "everything",
            },
            content_type="application/json",
        )

        assert response.status_code == 400, response.content
        assert "annotation_source_scope" in response.json()["errors"]

    def test_phi_region_detector_training_run_endpoints_create_run(self):
        dataset_yaml = Path("/tmp/phi-region-detector-dataset.yaml")

        from endoreg_db.views.misc import application_settings as view_module

        captured_kwargs: dict[str, object] = {}

        def fake_launch(run_id: str, *, command_kwargs: dict[str, object]) -> None:
            captured_kwargs.update(command_kwargs)
            run = AIModelTrainingRun.objects.get(run_id=run_id)
            run.status = AIModelTrainingRun.STATUS_COMPLETED
            run.started_at = timezone.now()
            run.finished_at = timezone.now()
            run.stdout = (
                "phi training finished\n"
                '{"model_path": "/tmp/phi.onnx", '
                '"checkpoint_path": "/tmp/best.pt", '
                '"meta_path": "/tmp/phi.json"}'
            )
            run.result = {
                "model_path": "/tmp/phi.onnx",
                "checkpoint_path": "/tmp/best.pt",
                "meta_path": "/tmp/phi.json",
            }
            run.artifact_paths = {
                "model_path": "/tmp/phi.onnx",
                "checkpoint_path": "/tmp/best.pt",
                "meta_path": "/tmp/phi.json",
            }
            run.error = ""
            run.save(
                update_fields=[
                    "status",
                    "started_at",
                    "finished_at",
                    "stdout",
                    "result",
                    "artifact_paths",
                    "error",
                    "updated_at",
                ]
            )

        original_launch = view_module._launch_model_training_run
        try:
            view_module._launch_model_training_run = fake_launch
            create_response = self.client.post(
                "/api/settings/application/model_training/runs/",
                data={
                    "training_target": "phi_region_detector",
                    "dataset_yaml": str(dataset_yaml),
                    "output_dir": "/tmp/phi-runs",
                    "base_model": "yolov8s.pt",
                    "run_name": "phi-smoke",
                    "epochs": 2,
                    "batch_size": 4,
                    "input_size": 512,
                    "device": "cpu",
                    "workers": 0,
                    "patience": 3,
                    "export_onnx": True,
                    "confidence_threshold": 0.4,
                    "nms_threshold": 0.5,
                    "class_ids": "0",
                },
                content_type="application/json",
            )
        finally:
            view_module._launch_model_training_run = original_launch

        assert create_response.status_code == 202, create_response.content
        created_payload = create_response.json()
        assert created_payload["training_target"] == "phi_region_detector"
        assert created_payload["dataset_name"] == dataset_yaml.name
        assert created_payload["backbone_name"] == "yolov8s.pt"
        assert created_payload["feature_mode"] == "yolo_onnx_detector"
        assert captured_kwargs["_command_name"] == "train_phi_region_detector"
        assert captured_kwargs["dataset_yaml"] == str(dataset_yaml)
        assert captured_kwargs["input_size"] == 512

        detail_response = self.client.get(
            f"/api/settings/application/model_training/runs/{created_payload['run_id']}/"
        )
        assert detail_response.status_code == 200, detail_response.content
        detail_payload = detail_response.json()
        assert detail_payload["artifact_paths"]["checkpoint_path"] == "/tmp/best.pt"
        assert detail_payload["result"]["model_path"] == "/tmp/phi.onnx"

    def test_model_training_run_execution_parses_stdout_json_result(self):
        dataset = AIDataSet.objects.create(
            name=f"train-parse-{uuid4().hex[:8]}",
            dataset_type=AIDataSet.DATASET_TYPE_IMAGE,
            ai_model_type=AIDataSet.AI_MODEL_TYPE_IMAGE_MULTILABEL,
        )
        run = AIModelTrainingRun.objects.create(
            dataset=dataset,
            dataset_name=dataset.name,
            dataset_type=dataset.dataset_type,
            ai_model_type=dataset.ai_model_type,
            backbone_name="gastro_rn50",
            feature_mode="freeze_backbone",
            freeze_backbone=True,
            epochs=1,
            batch_size=1,
            labelset_version=2,
            treat_unlabeled_as_negative=True,
            command_kwargs={"dataset_id": dataset.pk},
            server_instance_id=view_module._MODEL_TRAINING_SERVER_INSTANCE_ID,
        )

        with (
            TemporaryDirectory() as staging_root,
            override_settings(MODEL_TRAINING_STAGING_ROOT=Path(staging_root)),
            patch.object(model_training_jobs, "call_command") as mocked_call_command,
        ):

            def fake_call_command(*args, **kwargs):
                kwargs["stdout"].write(
                    'log line\n{"model_path": "/tmp/model.pth", '
                    '"manifest_path": "/tmp/manifest.json", '
                    '"meta_path": "/tmp/meta.json"}\n'
                )

            mocked_call_command.side_effect = fake_call_command
            model_training_jobs._execute_model_training_run(
                run.run_key,
                command_kwargs={"dataset_id": dataset.pk},
            )

        assert mocked_call_command.call_args.args[0] == "train_image_multilabel_model"
        run.refresh_from_db()
        assert run.status == AIModelTrainingRun.STATUS_COMPLETED
        assert run.result["model_path"] == "/tmp/model.pth"
        assert run.artifact_paths["manifest_path"] == "/tmp/manifest.json"

    def test_model_training_run_execution_stores_failure_logs(self):
        dataset = AIDataSet.objects.create(
            name=f"train-fail-{uuid4().hex[:8]}",
            dataset_type=AIDataSet.DATASET_TYPE_IMAGE,
            ai_model_type=AIDataSet.AI_MODEL_TYPE_IMAGE_MULTILABEL,
        )
        run = AIModelTrainingRun.objects.create(
            dataset=dataset,
            dataset_name=dataset.name,
            dataset_type=dataset.dataset_type,
            ai_model_type=dataset.ai_model_type,
            backbone_name="gastro_rn50",
            feature_mode="freeze_backbone",
            freeze_backbone=True,
            epochs=1,
            batch_size=1,
            labelset_version=2,
            treat_unlabeled_as_negative=True,
            command_kwargs={"dataset_id": dataset.pk},
            server_instance_id=view_module._MODEL_TRAINING_SERVER_INSTANCE_ID,
        )

        with (
            TemporaryDirectory() as staging_root,
            override_settings(MODEL_TRAINING_STAGING_ROOT=Path(staging_root)),
            patch.object(model_training_jobs, "call_command") as mocked_call_command,
        ):

            def fake_call_command(*args, **kwargs):
                kwargs["stdout"].write("training started")
                kwargs["stderr"].write("stderr detail")
                raise RuntimeError("boom")

            mocked_call_command.side_effect = fake_call_command
            model_training_jobs._execute_model_training_run(
                run.run_key,
                command_kwargs={"dataset_id": dataset.pk},
            )

        run.refresh_from_db()
        assert run.status == AIModelTrainingRun.STATUS_FAILED
        assert run.error == "boom"
        assert "training started" in run.stdout
        assert "stderr detail" in run.stdout

    def test_model_training_run_keeps_fresh_other_process_run_active(self):
        dataset = AIDataSet.objects.create(
            name=f"train-active-{uuid4().hex[:8]}",
            dataset_type=AIDataSet.DATASET_TYPE_IMAGE,
            ai_model_type=AIDataSet.AI_MODEL_TYPE_IMAGE_MULTILABEL,
        )
        run = AIModelTrainingRun.objects.create(
            dataset=dataset,
            dataset_name=dataset.name,
            dataset_type=dataset.dataset_type,
            ai_model_type=dataset.ai_model_type,
            backbone_name="gastro_rn50",
            feature_mode="freeze_backbone",
            freeze_backbone=True,
            epochs=1,
            batch_size=1,
            labelset_version=2,
            treat_unlabeled_as_negative=True,
            status=AIModelTrainingRun.STATUS_RUNNING,
            server_instance_id="old-process",
        )

        response = self.client.get(
            f"/api/settings/application/model_training/runs/{run.run_key}/"
        )

        assert response.status_code == 200, response.content
        payload = response.json()
        assert payload["status"] == "running"

        run.refresh_from_db()
        assert run.status == AIModelTrainingRun.STATUS_RUNNING

    def test_model_training_run_marks_stale_other_process_runs_lost(self):
        dataset = AIDataSet.objects.create(
            name=f"train-lost-{uuid4().hex[:8]}",
            dataset_type=AIDataSet.DATASET_TYPE_IMAGE,
            ai_model_type=AIDataSet.AI_MODEL_TYPE_IMAGE_MULTILABEL,
        )
        run = AIModelTrainingRun.objects.create(
            dataset=dataset,
            dataset_name=dataset.name,
            dataset_type=dataset.dataset_type,
            ai_model_type=dataset.ai_model_type,
            backbone_name="gastro_rn50",
            feature_mode="freeze_backbone",
            freeze_backbone=True,
            epochs=1,
            batch_size=1,
            labelset_version=2,
            treat_unlabeled_as_negative=True,
            status=AIModelTrainingRun.STATUS_RUNNING,
            server_instance_id="old-process",
        )
        AIModelTrainingRun.objects.filter(pk=run.pk).update(
            updated_at=(
                timezone.now()
                - view_module.MODEL_TRAINING_LOST_TIMEOUT
                - timedelta(minutes=1)
            )
        )

        response = self.client.get(
            f"/api/settings/application/model_training/runs/{run.run_key}/"
        )

        assert response.status_code == 200, response.content
        payload = response.json()
        assert payload["status"] == "lost"
        assert "LOST" in payload["error"]

    def test_video_dimension_backfill_run_endpoints_create_and_report_run(self):
        from endoreg_db.views.misc import application_settings as view_module

        def fake_launch(run_id: str, *, command_kwargs: dict[str, object]) -> None:
            view_module._store_video_dimension_backfill_run(
                run_id,
                status="completed",
                started_at="2026-04-29T10:00:01Z",
                finished_at="2026-04-29T10:00:30Z",
                result={
                    "count": 1,
                    "summary": {"would_repair": 1},
                    "items": [
                        {
                            "video_id": 123,
                            "status": "would_repair",
                            "source_dimensions": [1920, 1080],
                            "processed_dimensions": [1440, 1080],
                            "repaired": False,
                            "detail": "",
                        }
                    ],
                },
                error=None,
                stdout="",
            )

        original_launch = view_module._launch_video_dimension_backfill_run
        try:
            view_module._launch_video_dimension_backfill_run = fake_launch
            create_response = self.client.post(
                "/api/settings/application/video_dimension_backfill/runs/",
                data={"dry_run": True, "limit": 5},
                content_type="application/json",
            )
        finally:
            view_module._launch_video_dimension_backfill_run = original_launch

        assert create_response.status_code == 202, create_response.content
        created_payload = create_response.json()
        assert created_payload["dry_run"] is True
        assert created_payload["limit"] == 5

        detail_response = self.client.get(
            "/api/settings/application/video_dimension_backfill/runs/"
            f"{created_payload['run_id']}/"
        )
        assert detail_response.status_code == 200, detail_response.content
        detail_payload = detail_response.json()
        assert detail_payload["status"] == "completed"
        assert detail_payload["result"]["summary"] == {"would_repair": 1}
        assert detail_payload["result"]["items"][0]["video_id"] == 123

    def test_video_dimension_backfill_run_rejects_bad_payload(self):
        response = self.client.post(
            "/api/settings/application/video_dimension_backfill/runs/",
            data={"dry_run": "yes", "limit": 0},
            content_type="application/json",
        )

        assert response.status_code == 400, response.content
        errors = response.json()["errors"]
        assert errors["dry_run"] == "dry_run must be a boolean."
        assert errors["limit"] == "limit must be a positive integer."

    def test_train_image_multilabel_model_command_forwards_selected_backbone_and_feature_mode(
        self,
    ):
        dataset = AIDataSet.objects.create(
            name=f"command-train-{uuid4().hex[:8]}",
            dataset_type=AIDataSet.DATASET_TYPE_IMAGE,
            ai_model_type=AIDataSet.AI_MODEL_TYPE_IMAGE_MULTILABEL,
        )

        with patch(
            "endoreg_db.management.commands.train_image_multilabel_model.train_gastronet_multilabel"
        ) as mocked_trainer:
            mocked_trainer.return_value = {
                "model_path": "/tmp/model.pth",
                "meta_path": "/tmp/meta.json",
            }
            call_command(
                "train_image_multilabel_model",
                dataset_id=dataset.pk,
                backbone_name="efficientnet_b0_imagenet",
                freeze_backbone=False,
                epochs=4,
                batch_size=16,
                labelset_version=3,
                device="cpu",
                annotation_source_scope="frame_only",
                treat_unlabeled_as_negative=False,
            )

        config = mocked_trainer.call_args.args[0]
        assert config.dataset_id == dataset.pk
        assert config.backbone_name == "efficientnet_b0_imagenet"
        assert config.freeze_backbone is False
        assert config.num_epochs == 4
        assert config.batch_size == 16
        assert config.labelset_version_to_train == 3
        assert config.device == "cpu"
        assert config.annotation_source_scope == "frame_only"
        assert config.treat_unlabeled_as_negative is False

    def test_application_settings_backup_endpoint(self):
        from endoreg_db.views.misc import application_settings as view_module

        with (
            TemporaryDirectory() as storage_dir,
            TemporaryDirectory() as target_dir,
        ):
            storage_path = Path(storage_dir)
            target_path = Path(target_dir)
            (storage_path / "alpha.txt").write_text("alpha", encoding="utf-8")
            original_sources = view_module._required_backup_sources
            try:
                view_module._required_backup_sources = lambda: [storage_path]
                response = self.client.post(
                    "/api/settings/application/backup/",
                    data={"target_path": str(target_path)},
                    content_type="application/json",
                )
            finally:
                view_module._required_backup_sources = original_sources

            assert response.status_code == 201, response.content
            payload = response.json()
            backup_root = Path(payload["target_root"])
            assert backup_root.exists()
            assert (backup_root / "storage" / "alpha.txt").read_text(
                encoding="utf-8"
            ) == "alpha"
            assert (backup_root / "manifest.json").exists()

    def test_application_settings_backup_requires_absolute_target(self):
        response = self.client.post(
            "/api/settings/application/backup/",
            data={"target_path": "relative/backup"},
            content_type="application/json",
        )
        assert response.status_code == 400, response.content
        assert "target_path" in response.json()["errors"]

    def test_application_settings_backup_rejects_live_data_child_target(self):
        from endoreg_db.views.misc import application_settings as view_module

        with TemporaryDirectory() as storage_dir:
            storage_path = Path(storage_dir)
            original_sources = view_module._required_backup_sources
            try:
                view_module._required_backup_sources = lambda: [storage_path]
                response = self.client.post(
                    "/api/settings/application/backup/",
                    data={"target_path": str(storage_path / "nested-backup")},
                    content_type="application/json",
                )
            finally:
                view_module._required_backup_sources = original_sources

        assert response.status_code == 400, response.content
        assert "live data roots" in response.json()["errors"]["target_path"]

    def test_network_node_settings_endpoints(self):
        list_response = self.client.get("/api/settings/application/network_nodes/")
        assert list_response.status_code == 200, list_response.content
        assert list_response.json() == []

        create_response = self.client.post(
            "/api/settings/application/network_nodes/",
            data={
                "display_name": "Study Hub",
                "role": NetworkNode.Role.CENTRAL_HUB,
                "base_url": "https://hub.example.org",
                "owning_center_id": self.center.pk,
                "shared_secret": "secret-123",
            },
            content_type="application/json",
        )
        assert create_response.status_code == 201, create_response.content
        payload = create_response.json()
        assert payload["display_name"] == "Study Hub"
        assert payload["role"] == NetworkNode.Role.CENTRAL_HUB
        assert payload["owning_center_id"] == self.center.pk
        assert payload["has_shared_secret"] is True
        node_id = payload["id"]

        detail_response = self.client.get(
            f"/api/settings/application/network_nodes/{node_id}/"
        )
        assert detail_response.status_code == 200, detail_response.content
        assert detail_response.json()["node_key"]

        patch_response = self.client.patch(
            f"/api/settings/application/network_nodes/{node_id}/",
            data={
                "display_name": "Site A",
                "role": NetworkNode.Role.SITE_NODE,
                "is_active": False,
                "clear_shared_secret": True,
            },
            content_type="application/json",
        )
        assert patch_response.status_code == 200, patch_response.content
        patched_payload = patch_response.json()
        assert patched_payload["display_name"] == "Site A"
        assert patched_payload["role"] == NetworkNode.Role.SITE_NODE
        assert patched_payload["is_active"] is False
        assert patched_payload["has_shared_secret"] is False

        roles_response = self.client.get(
            "/api/settings/application/dropdowns/network_node_roles/"
        )
        assert roles_response.status_code == 200, roles_response.content
        assert any(
            entry["value"] == NetworkNode.Role.CENTRAL_HUB
            for entry in roles_response.json()
        )

        delete_response = self.client.delete(
            f"/api/settings/application/network_nodes/{node_id}/"
        )
        assert delete_response.status_code == 204, delete_response.content
        assert not NetworkNode.objects.filter(pk=node_id).exists()

    def test_network_node_patch_rejects_node_key_change(self):
        node = NetworkNode.objects.create(
            display_name="Existing Node",
            role=NetworkNode.Role.SITE_NODE,
        )

        response = self.client.patch(
            f"/api/settings/application/network_nodes/{node.pk}/",
            data={"node_key": "changed-key"},
            content_type="application/json",
        )
        assert response.status_code == 400, response.content
        assert "node_key" in response.json()["errors"]

    def test_network_node_create_rejects_duplicate_node_key_and_bad_types(self):
        existing = NetworkNode.objects.create(
            display_name="Existing Node",
            role=NetworkNode.Role.SITE_NODE,
            node_key="fixed-node-key",
        )

        response = self.client.post(
            "/api/settings/application/network_nodes/",
            data={
                "display_name": "Duplicate Node",
                "role": "not-a-role",
                "node_key": existing.node_key,
                "is_active": "yes",
                "shared_secret": 123,
            },
            content_type="application/json",
        )

        assert response.status_code == 400, response.content
        errors = response.json()["errors"]
        assert errors["role"] == "Invalid role."
        assert errors["node_key"] == "node_key already exists."
        assert errors["is_active"] == "is_active must be a boolean."
        assert errors["shared_secret"] == "shared_secret must be a string."
