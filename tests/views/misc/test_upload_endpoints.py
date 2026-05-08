from __future__ import annotations

from unittest.mock import patch
from uuid import uuid4

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.test.utils import override_settings

from endoreg_db.models import (
    ApplicationSettings,
    Center,
    Examiner,
    NetworkNode,
    PortalUserInfo,
    UploadJob,
)

MINIMAL_PDF_BYTES = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"


class UploadEndpointTests(TestCase):
    def test_upload_rejects_missing_file(self):
        response = self.client.post("/api/upload/", data={})
        assert response.status_code == 400, response.content
        assert "No file provided" in response.json()["error"]

    def test_upload_status_returns_404_for_unknown_job(self):
        response = self.client.get(f"/api/upload/{uuid4()}/status/")
        assert response.status_code == 404, response.content

    def test_upload_pdf_creates_job_and_status_is_available(self):
        uploaded = SimpleUploadedFile(
            name="upload-test.pdf",
            content=MINIMAL_PDF_BYTES,
            content_type="application/pdf",
        )

        with (
            patch("endoreg_db.views.misc.upload_views.CELERY_AVAILABLE", False),
            patch(
                "endoreg_db.views.misc.upload_views.start_upload_job_processing",
                return_value="inline",
            ),
        ):
            response = self.client.post("/api/upload/", data={"file": uploaded})

        assert response.status_code == 201, response.content
        payload = response.json()
        assert "upload_id" in payload
        assert "status_url" in payload
        assert payload["status_url"].endswith(f"/upload/{payload['upload_id']}/status/")

        status_response = self.client.get(payload["status_url"])
        assert status_response.status_code == 200, status_response.content
        status_payload = status_response.json()
        assert status_payload["id"] == payload["upload_id"]
        assert status_payload["status"] == "pending"

    def test_upload_reuses_existing_job_for_same_idempotency_key(self):
        uploaded_a = SimpleUploadedFile(
            name="upload-a.pdf",
            content=MINIMAL_PDF_BYTES,
            content_type="application/pdf",
        )
        uploaded_b = SimpleUploadedFile(
            name="upload-b.pdf",
            content=MINIMAL_PDF_BYTES,
            content_type="application/pdf",
        )

        with (
            patch("endoreg_db.views.misc.upload_views.CELERY_AVAILABLE", False),
            patch(
                "endoreg_db.views.misc.upload_views.start_upload_job_processing",
                return_value="inline",
            ),
        ):
            first = self.client.post(
                "/api/upload/",
                data={"file": uploaded_a, "source_system": "site-a"},
                HTTP_IDEMPOTENCY_KEY="same-logical-upload",
            )
            second = self.client.post(
                "/api/upload/",
                data={"file": uploaded_b, "source_system": "site-a"},
                HTTP_IDEMPOTENCY_KEY="same-logical-upload",
            )

        assert first.status_code == 201, first.content
        assert second.status_code == 200, second.content
        assert first.json()["upload_id"] == second.json()["upload_id"]

    def test_upload_reuses_existing_job_for_same_content_hash(self):
        uploaded_a = SimpleUploadedFile(
            name="upload-a.pdf",
            content=MINIMAL_PDF_BYTES,
            content_type="application/pdf",
        )
        uploaded_b = SimpleUploadedFile(
            name="renamed-upload.pdf",
            content=MINIMAL_PDF_BYTES,
            content_type="application/pdf",
        )

        with (
            patch("endoreg_db.views.misc.upload_views.CELERY_AVAILABLE", False),
            patch(
                "endoreg_db.views.misc.upload_views.start_upload_job_processing",
                return_value="inline",
            ),
        ):
            first = self.client.post(
                "/api/upload/",
                data={"file": uploaded_a, "source_system": "site-a"},
            )
            second = self.client.post(
                "/api/upload/",
                data={"file": uploaded_b, "source_system": "site-b"},
            )

        assert first.status_code == 201, first.content
        assert second.status_code == 200, second.content
        assert first.json()["upload_id"] == second.json()["upload_id"]

    def test_upload_rejects_unknown_declared_center(self):
        uploaded = SimpleUploadedFile(
            name="upload-test.pdf",
            content=MINIMAL_PDF_BYTES,
            content_type="application/pdf",
        )

        with (
            patch("endoreg_db.views.misc.upload_views.CELERY_AVAILABLE", False),
            patch(
                "endoreg_db.views.misc.upload_views.start_upload_job_processing",
                return_value="inline",
            ),
        ):
            response = self.client.post(
                "/api/upload/",
                data={"file": uploaded, "center_key": "missing-center"},
            )

        assert response.status_code == 400, response.content
        assert "Unknown center_key" in response.json()["error"]

    def test_upload_uses_default_center_when_none_declared(self):
        default_center = Center.objects.create(
            name="default-center",
            display_name="Default Center",
        )
        settings_obj = ApplicationSettings.get_solo()
        settings_obj.center = default_center
        settings_obj.save()

        uploaded = SimpleUploadedFile(
            name="upload-test.pdf",
            content=MINIMAL_PDF_BYTES,
            content_type="application/pdf",
        )

        with (
            patch("endoreg_db.views.misc.upload_views.CELERY_AVAILABLE", False),
            patch(
                "endoreg_db.views.misc.upload_views.start_upload_job_processing",
                return_value="inline",
            ),
        ):
            response = self.client.post("/api/upload/", data={"file": uploaded})

        assert response.status_code == 201, response.content
        upload_job = UploadJob.objects.get(id=response.json()["upload_id"])
        assert upload_job.source_center == default_center

    @override_settings(ENDOREG_DEPLOYMENT_ROLE="central_hub")
    def test_hub_mode_rejects_unauthenticated_upload(self):
        uploaded = SimpleUploadedFile(
            name="upload-test.pdf",
            content=MINIMAL_PDF_BYTES,
            content_type="application/pdf",
        )

        with (
            patch("endoreg_db.views.misc.upload_views.CELERY_AVAILABLE", False),
            patch(
                "endoreg_db.views.misc.upload_views.start_upload_job_processing",
                return_value="inline",
            ),
        ):
            response = self.client.post(
                "/api/upload/",
                data={"file": uploaded, "center_key": "any-center"},
            )

        assert response.status_code == 403, response.content
        assert "Authentication is required" in response.json()["error"]

    @override_settings(ENDOREG_DEPLOYMENT_ROLE="central_hub")
    def test_hub_mode_requires_declared_center_key(self):
        user = User.objects.create_user(username="hub-user", password="secret")
        self.client.force_login(user)
        uploaded = SimpleUploadedFile(
            name="upload-test.pdf",
            content=MINIMAL_PDF_BYTES,
            content_type="application/pdf",
        )

        with (
            patch("endoreg_db.views.misc.upload_views.CELERY_AVAILABLE", False),
            patch(
                "endoreg_db.views.misc.upload_views.start_upload_job_processing",
                return_value="inline",
            ),
        ):
            response = self.client.post(
                "/api/upload/",
                data={"file": uploaded},
            )

        assert response.status_code == 400, response.content
        assert "center_key is required" in response.json()["error"]

    @override_settings(
        ENDOREG_DEPLOYMENT_ROLE="central_hub",
        ENDOREG_HUB_TRANSFER_REQUIRE_SECURE_TRANSPORT=False,
        ENDOREG_HUB_TRANSFER_REQUIRE_MTLS=False,  # Disable SSL check, but keep logic check
    )
    def test_hub_mode_accepts_authenticated_upload_with_center_key(self):
        # 1. Setup the Center and User
        center = Center.objects.create(name="hub-center", display_name="Hub Center")
        user = User.objects.create_user(username="hub-user", password="secret")

        examiner = Examiner.objects.create(
            first_name="Hub", last_name="User", hash="hub-user-hash", center=center
        )
        PortalUserInfo.objects.create(user=user, examiner=examiner)

        # 2. Setup the Machine (The NetworkNode)
        node = NetworkNode.objects.create(node_key="test-node-123", is_active=True)
        node.set_shared_secret("super-secret-key")
        node.save()

        self.client.force_login(user)
        uploaded = SimpleUploadedFile(
            name="upload-test.pdf",
            content=MINIMAL_PDF_BYTES,
            content_type="application/pdf",
        )

        with (
            patch("endoreg_db.views.misc.upload_views.CELERY_AVAILABLE", False),
            patch(
                "endoreg_db.views.misc.upload_views.start_upload_job_processing",
                return_value="inline",
            ),
        ):
            # 3. Pass the Node Headers. This satisfies _enforce_transfer_node_auth
            response = self.client.post(
                "/api/upload/",
                data={
                    "file": uploaded,
                    "center_key": center.center_key,
                },
                HTTP_X_NETWORK_NODE_KEY=node.node_key,
                HTTP_X_NETWORK_NODE_SECRET="super-secret-key",
            )

        assert response.status_code == 201, response.content

    @override_settings(ENDOREG_DEPLOYMENT_ROLE="central_hub")
    def test_hub_mode_rejects_authenticated_upload_without_center_scope(self):
        center = Center.objects.create(name="hub-center", display_name="Hub Center")
        user = User.objects.create_user(
            username="unscoped-hub-user",
            password="secret",
        )
        self.client.force_login(user)
        uploaded = SimpleUploadedFile(
            name="upload-test.pdf",
            content=MINIMAL_PDF_BYTES,
            content_type="application/pdf",
        )

        with (
            patch("endoreg_db.views.misc.upload_views.CELERY_AVAILABLE", False),
            patch(
                "endoreg_db.views.misc.upload_views.start_upload_job_processing",
                return_value="inline",
            ),
        ):
            response = self.client.post(
                "/api/upload/",
                data={"file": uploaded, "center_key": center.center_key},
            )

        assert response.status_code == 403, response.content
        assert "do not have access" in response.json()["error"]

    # ADD THIS TO PREVENT USERS FROM UPLOADING DATA
    # def test_upload_rejects_authenticated_center_override(self):
    #     center = Center.objects.create(name="hub-center", display_name="Hub Center")
    #     user = User.objects.create_user(username="hub-user", password="secret")

    #     # --- NEW: Link the User to the Center ---
    #     examiner = Examiner.objects.create(
    #         first_name="Hub",
    #         last_name="User",
    #         hash="hub-user-hash",
    #         center=center  # This is the crucial link
    #     )
    #     PortalUserInfo.objects.create(user=user, examiner=examiner)

    #     uploaded = SimpleUploadedFile(
    #         name="upload-test.pdf",
    #         content=MINIMAL_PDF_BYTES,
    #         content_type="application/pdf",
    #     )

    #     self.client.force_login(user)
    #     with (
    #         patch("endoreg_db.views.misc.upload_views.CELERY_AVAILABLE", False),
    #         patch(
    #             "endoreg_db.views.misc.upload_views.start_upload_job_processing",
    #             return_value="inline",
    #         ),
    #     ):
    #         response = self.client.post(
    #             "/api/upload/",
    #             data={"file": uploaded, "center_key": center.center_key},
    #         )

    #     assert response.status_code == 403, response.content

    def test_upload_status_is_center_scoped_for_authenticated_users(self):
        center_a = Center.objects.create(name="center-a", display_name="Center A")
        center_b = Center.objects.create(name="center-b", display_name="Center B")

        examiner = Examiner.objects.create(
            first_name="Scoped",
            last_name="User",
            hash="scoped-user-hash",
            center=center_b,
        )
        user = User.objects.create_user(username="scoped-user", password="secret")
        PortalUserInfo.objects.create(user=user, examiner=examiner)

        upload_job = UploadJob.objects.create(
            file=SimpleUploadedFile(
                name="status-scope.pdf",
                content=MINIMAL_PDF_BYTES,
                content_type="application/pdf",
            ),
            content_type="application/pdf",
            source_center=center_a,
            source_system="site-a",
        )

        self.client.force_login(user)
        response = self.client.get(f"/api/upload/{upload_job.id}/status/")
        assert response.status_code == 404, response.content

    def test_upload_dispatches_inline_processing_when_celery_is_unavailable(self):
        uploaded = SimpleUploadedFile(
            name="upload-test.pdf",
            content=MINIMAL_PDF_BYTES,
            content_type="application/pdf",
        )

        with (
            patch("endoreg_db.views.misc.upload_views.CELERY_AVAILABLE", False),
            patch(
                "endoreg_db.views.misc.upload_views.start_upload_job_processing",
                return_value="inline",
            ) as start_processing,
        ):
            response = self.client.post("/api/upload/", data={"file": uploaded})

        assert response.status_code == 201, response.content
        upload_job = UploadJob.objects.get(id=response.json()["upload_id"])
        assert upload_job.storage_class == UploadJob.StorageClass.INGEST
        assert upload_job.storage_tier == UploadJob.StorageTier.UPLOAD_API
        assert upload_job.retention_policy == UploadJob.RetentionPolicy.PRESERVE_SOURCE
        assert upload_job.cleanup_status == UploadJob.CleanupStatus.PENDING
        start_processing.assert_called_once_with(
            upload_job=upload_job,
            task_dispatcher=None,
        )

    def test_upload_returns_500_when_processing_handoff_fails(self):
        uploaded = SimpleUploadedFile(
            name="upload-test.pdf",
            content=MINIMAL_PDF_BYTES,
            content_type="application/pdf",
        )

        with (
            patch("endoreg_db.views.misc.upload_views.CELERY_AVAILABLE", False),
            patch(
                "endoreg_db.views.misc.upload_views.start_upload_job_processing",
                side_effect=RuntimeError("inline processing failed"),
            ),
        ):
            response = self.client.post("/api/upload/", data={"file": uploaded})

        assert response.status_code == 500, response.content
        assert (
            "Failed to start processing: inline processing failed"
            in response.json()["error"]
        )
