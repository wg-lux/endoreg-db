# endoreg_db/services/export_annotated.py

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from django.contrib.auth.models import AnonymousUser, User
from django.core.exceptions import PermissionDenied, ValidationError

from lx_dtypes.models.contracts.export_annotated import ExportAnnotatedConfigContract
from lx_dtypes.models.contracts.video_frame_export import export_config, export_result
from endoreg_db.export.frames.export_frames_with_labels import (
    annotation_exporter_client,
    export_job_failed_error,
)
from endoreg_db.models.administration.center.center import Center
from endoreg_db.models.media.video.video_file import VideoFile
from endoreg_db.services.hub.deployment import local_study_server_mode_enabled
from endoreg_db.services.center_access import resolve_allowed_center_ids

logger = logging.getLogger(__name__)


class ExportConflictError(RuntimeError):
    pass


@dataclass(slots=True)
class ExportAnnotatedService:
    client: annotation_exporter_client

    @classmethod
    def default(cls) -> ExportAnnotatedService:
        return cls(client=annotation_exporter_client())

    def run_api_export(
        self,
        *,
        payload: dict[str, Any],
        user: User | AnonymousUser,
    ) -> export_result:
        contract = ExportAnnotatedConfigContract.from_api_payload(payload)

        video = VideoFile.objects.select_related("state", "center").get(
            pk=contract.video_id
        )
        self._validate_scope_after_loading(config=contract, user=user, video=video)
        self._validate_video_specific_export(video)

        config: export_config = contract.to_export_config()
        try:
            return self.client.run_export(config)
        except export_job_failed_error:
            raise
        except Exception as exc:
            logger.exception("Annotated export failed")
            raise export_job_failed_error(
                "annotation export failed",
                original_error=exc,
            ) from exc

    def _validate_scope_after_loading(
        self,
        *,
        config: ExportAnnotatedConfigContract,
        user: User | AnonymousUser,
        video: VideoFile,
    ) -> None:
        if config.center_key:
            if not Center.objects.filter(center_key=config.center_key).exists():
                raise ValidationError(f"Unknown center_key: {config.center_key}")

        if config.all_centers and not getattr(user, "is_staff", False):
            raise PermissionDenied("all_centers export is only allowed for staff users")

        allowed_center_ids = resolve_allowed_center_ids(user)
        if allowed_center_ids is not None and video.center_id not in allowed_center_ids:
            raise PermissionDenied("video is outside the authenticated center scope")

        if config.center_key and config.center_key != video.center.center_key:
            raise PermissionDenied("center_key does not match the requested video")

        if not local_study_server_mode_enabled():
            return

        if not (bool(config.center_key) ^ bool(config.all_centers)):
            raise PermissionDenied(
                "local_study_server exports require exactly one center scope: "
                "center_key or all_centers"
            )

    def _validate_video_specific_export(
        self,
        video: VideoFile,
    ) -> None:
        state = getattr(video, "state", None)

        if state is None:
            return

        segment_annotations_created = bool(
            getattr(state, "segment_annotations_created", False)
        )
        segment_annotations_validated = bool(
            getattr(state, "segment_annotations_validated", False)
        )

        if segment_annotations_created and not segment_annotations_validated:
            raise ExportConflictError(
                "cleanup_required: segment annotations are not finalized"
            )
