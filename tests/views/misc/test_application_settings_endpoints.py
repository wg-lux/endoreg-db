from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from uuid import uuid4

from django.core.management import call_command
from django.contrib.auth import get_user_model
from django.test import TestCase

from endoreg_db.models import AIDataSet, Center, EndoscopyProcessor, NetworkNode


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
                "ai_dataset_name": dataset.name,
                "ai_dataset_type": dataset.dataset_type,
            },
            content_type="application/json",
        )
        assert response.status_code == 201, response.content
        payload = response.json()
        assert payload["success"] is True
        assert payload["dataset_id"] == dataset.pk
        output_path = Path(payload["output_path"])
        assert output_path.exists()
        exported = output_path.read_text(encoding="utf-8")
        assert dataset.name in exported

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

        def fake_launch(run_id: str, *, command_kwargs: dict[str, object]) -> None:
            view_module._store_model_training_run(
                run_id,
                status="completed",
                started_at="2026-04-17T10:00:01Z",
                finished_at="2026-04-17T10:00:30Z",
                stdout="training finished",
                result={
                    "model_path": "/tmp/model.pth",
                    "meta_path": "/tmp/meta.json",
                },
                error=None,
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

        detail_response = self.client.get(
            f"/api/settings/application/model_training/runs/{created_payload['run_id']}/"
        )
        assert detail_response.status_code == 200, detail_response.content
        detail_payload = detail_response.json()
        assert detail_payload["status"] == "completed"
        assert detail_payload["result"]["model_path"] == "/tmp/model.pth"
        assert "training finished" in detail_payload["stdout"]

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
                treat_unlabeled_as_negative=False,
            )

        config = mocked_trainer.call_args.args[0]
        assert config.dataset_id == dataset.pk
        assert config.backbone_name == "efficientnet_b0_imagenet"
        assert config.freeze_backbone is False
        assert config.num_epochs == 4
        assert config.batch_size == 16
        assert config.labelset_version_to_train == 3
        assert config.treat_unlabeled_as_negative is False

    def test_application_settings_backup_endpoint(self):
        from endoreg_db.views.misc import application_settings as view_module

        with (
            TemporaryDirectory() as storage_dir,
            TemporaryDirectory() as io_dir,
            TemporaryDirectory() as target_dir,
        ):
            storage_path = Path(storage_dir)
            io_path = Path(io_dir)
            target_path = Path(target_dir)
            (storage_path / "alpha.txt").write_text("alpha", encoding="utf-8")
            (io_path / "beta.txt").write_text("beta", encoding="utf-8")

            original_sources = view_module._required_backup_sources
            try:
                view_module._required_backup_sources = lambda: [storage_path, io_path]
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
            assert (backup_root / "io" / "beta.txt").read_text(
                encoding="utf-8"
            ) == "beta"
            assert (backup_root / "manifest.json").exists()

    def test_application_settings_backup_requires_absolute_target(self):
        response = self.client.post(
            "/api/settings/application/backup/",
            data={"target_path": "relative/backup"},
            content_type="application/json",
        )
        assert response.status_code == 400, response.content
        assert "target_path" in response.json()["errors"]

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
