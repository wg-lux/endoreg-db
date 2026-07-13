from __future__ import annotations

from typing import TypedDict, cast

from rest_framework import serializers

from endoreg_db.models.administration.center.center import Center
from endoreg_db.models.hub.network_node import NetworkNode
from endoreg_db.models.hub.transfer_job import TransferJob
from endoreg_db.models.state.anonymization import AnonymizationState
from endoreg_db.schemas import (
    validate_transfer_processing_snapshot,
    validate_transfer_resource_rows,
)


class _VideoFilePayload(TypedDict):
    video_hash: str
    processed_video_hash: str


class _ReportFilePayload(TypedDict):
    pdf_hash: str


class _SensitiveMetaPayload(TypedDict, total=False):
    patient_hash: str
    examination_hash: str


class TransferJobCreateSerializer(serializers.Serializer[dict[str, object]]):
    transfer_key = serializers.CharField(max_length=255)
    source_node_key = serializers.CharField(max_length=255)
    target_node_key = serializers.CharField(
        max_length=255,
        required=False,
        allow_blank=True,
    )
    source_center_key = serializers.CharField(
        max_length=255,
        required=False,
        allow_blank=True,
    )
    resource_kind = serializers.ChoiceField(choices=TransferJob.ResourceKind.choices)
    resource_hash = serializers.CharField(max_length=255)
    transfer_mode = serializers.ChoiceField(
        choices=TransferJob.TransferMode.choices,
        default=TransferJob.TransferMode.METADATA_ONLY,
    )
    processing_policy = serializers.ChoiceField(
        choices=TransferJob.ProcessingPolicy.choices,
        default=TransferJob.ProcessingPolicy.PRESERVE_PROCESSING_STATE,
    )
    processing_intent = serializers.ChoiceField(
        choices=TransferJob.ProcessingIntent.choices,
        default=TransferJob.ProcessingIntent.STATE_PRESERVATION,
    )
    cleanup_policy = serializers.ChoiceField(
        choices=TransferJob.CleanupPolicy.choices,
        default=TransferJob.CleanupPolicy.RETAIN_ALL,
    )
    payload_schema_version = serializers.CharField(max_length=32, default="1.0")
    resource_rows = serializers.JSONField(default=dict)
    processing_snapshot = serializers.JSONField(default=dict)
    provenance = serializers.JSONField(default=dict, required=False)

    _TRANSFER_ELIGIBLE_ANONYMIZATION_STATES = {AnonymizationState.VALIDATED}

    def validate_payload_schema_version(self, value: str) -> str:
        normalized = value.strip()
        if normalized != "2.0":
            raise serializers.ValidationError(
                "Only privacy-preserving hub payload_schema_version '2.0' is accepted."
            )
        return normalized

    def validate_source_node_key(self, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise serializers.ValidationError("source_node_key is required")
        if not NetworkNode.objects.filter(node_key=normalized, is_active=True).exists():
            raise serializers.ValidationError(
                f"Unknown active source_node_key: {normalized}"
            )
        return normalized

    def validate(self, attrs: dict[str, object]) -> dict[str, object]:
        transfer_mode = str(attrs["transfer_mode"])

        if transfer_mode in {
            TransferJob.TransferMode.METADATA_AND_RAW_MEDIA.value,
            TransferJob.TransferMode.METADATA_RAW_AND_PROCESSED_MEDIA.value,
        }:
            raise serializers.ValidationError(
                {
                    "transfer_mode": (
                        "Raw media transfer is not permitted. "
                        "Only anonymized metadata or anonymized processed media may be transferred."
                    )
                }
            )

        source_node = NetworkNode.objects.get(
            node_key=attrs["source_node_key"],
            is_active=True,
        )
        target_node_key = str(attrs.get("target_node_key", "")).strip()
        if target_node_key:
            target_node = NetworkNode.objects.filter(
                node_key=target_node_key,
                is_active=True,
            ).first()
            if target_node is None:
                raise serializers.ValidationError(
                    {
                        "target_node_key": (
                            f"Unknown active target_node_key: {target_node_key}"
                        )
                    }
                )
        else:
            target_node = (
                NetworkNode.objects.filter(
                    role=NetworkNode.Role.CENTRAL_HUB,
                    is_active=True,
                )
                .order_by("pk")
                .first()
            )
            if target_node is None:
                raise serializers.ValidationError(
                    {
                        "target_node_key": (
                            "No active central_hub network node is configured"
                        )
                    }
                )

        source_center_key = str(attrs.get("source_center_key", "")).strip()
        source_center = None
        if source_center_key:
            source_center = Center.objects.filter(center_key=source_center_key).first()
            if source_center is None:
                raise serializers.ValidationError(
                    {"source_center_key": f"Unknown center_key: {source_center_key}"}
                )
        elif source_node.owning_center is not None:
            source_center = source_node.owning_center

        resource_rows = cast(dict[str, object], attrs.get("resource_rows", {}))
        self._validate_privacy_boundary(resource_rows)
        if transfer_mode in {
            TransferJob.TransferMode.METADATA_AND_RAW_MEDIA.value,
            TransferJob.TransferMode.METADATA_RAW_AND_PROCESSED_MEDIA.value,
        }:
            raise serializers.ValidationError(
                {
                    "transfer_mode": (
                        "Raw media transfer is not permitted. "
                        "Only anonymized metadata or anonymized processed media may be transferred."
                    )
                }
            )

        if str(attrs["resource_kind"]) == TransferJob.ResourceKind.VIDEO.value:
            video_file = cast(
                _VideoFilePayload | None,
                resource_rows.get("video_file"),
            )
            if video_file is None:
                raise serializers.ValidationError(
                    {
                        "resource_rows": (
                            "resource_rows.video_file is required for video transfers"
                        )
                    }
                )
            video_hash = video_file["video_hash"].strip()
            if not video_hash:
                raise serializers.ValidationError(
                    {
                        "resource_rows": (
                            "resource_rows.video_file.video_hash is required"
                        )
                    }
                )
            if video_hash != attrs["resource_hash"]:
                raise serializers.ValidationError(
                    {
                        "resource_hash": (
                            "resource_hash must match "
                            "resource_rows.video_file.video_hash"
                        )
                    }
                )
            if transfer_mode in {
                TransferJob.TransferMode.METADATA_AND_PROCESSED_MEDIA.value,
                TransferJob.TransferMode.METADATA_RAW_AND_PROCESSED_MEDIA.value,
            }:
                processed_video_hash = video_file.get(
                    "processed_video_hash", ""
                ).strip()
                if not processed_video_hash:
                    raise serializers.ValidationError(
                        {
                            "resource_rows": (
                                "resource_rows.video_file.processed_video_hash is "
                                "required when processed video media will be uploaded"
                            )
                        }
                    )
            video_state_payload = cast(
                dict[str, object], resource_rows.get("video_state", {})
            )
            anonymization_status = self._resolve_video_anonymization_status(
                video_state_payload
            )
            self._validate_transfer_eligible_anonymization_status(
                anonymization_status=anonymization_status,
                resource_kind="video",
            )
            if (
                transfer_mode
                == (TransferJob.TransferMode.METADATA_AND_PROCESSED_MEDIA.value)
                and not str(
                    video_state_payload.get("processed_file_sha256", "") or ""
                ).strip()
            ):
                raise serializers.ValidationError(
                    {
                        "resource_rows": (
                            "resource_rows.video_state.processed_file_sha256 is "
                            "required for processed-media transfer"
                        )
                    }
                )
        elif str(attrs["resource_kind"]) == TransferJob.ResourceKind.REPORT.value:
            report_file = cast(
                _ReportFilePayload | None, resource_rows.get("raw_pdf_file")
            )
            if report_file is None:
                raise serializers.ValidationError(
                    {
                        "resource_rows": (
                            "resource_rows.raw_pdf_file is required for report transfers"
                        )
                    }
                )
            pdf_hash = report_file["pdf_hash"].strip()
            if not pdf_hash:
                raise serializers.ValidationError(
                    {
                        "resource_rows": (
                            "resource_rows.raw_pdf_file.pdf_hash is required"
                        )
                    }
                )
            if pdf_hash != attrs["resource_hash"]:
                raise serializers.ValidationError(
                    {
                        "resource_hash": (
                            "resource_hash must match "
                            "resource_rows.raw_pdf_file.pdf_hash"
                        )
                    }
                )
            report_state_payload = cast(
                dict[str, object], resource_rows.get("raw_pdf_state", {})
            )
            anonymization_status = self._resolve_report_anonymization_status(
                report_state_payload
            )
            self._validate_transfer_eligible_anonymization_status(
                anonymization_status=anonymization_status,
                resource_kind="report",
            )
            if (
                transfer_mode
                == (TransferJob.TransferMode.METADATA_AND_PROCESSED_MEDIA.value)
                and not str(
                    report_state_payload.get("processed_file_sha256", "") or ""
                ).strip()
            ):
                raise serializers.ValidationError(
                    {
                        "resource_rows": (
                            "resource_rows.raw_pdf_state.processed_file_sha256 is "
                            "required for processed-media transfer"
                        )
                    }
                )

        try:
            attrs["resource_rows"] = validate_transfer_resource_rows(
                resource_rows,
                resource_kind=str(attrs["resource_kind"]),
            )
            attrs["processing_snapshot"] = validate_transfer_processing_snapshot(
                attrs.get("processing_snapshot", {})
            )
        except ValueError as exc:
            raise serializers.ValidationError({"resource_rows": str(exc)}) from exc

        self._validate_sensitive_meta_linkage(resource_rows)

        attrs["source_node"] = source_node
        attrs["target_node"] = target_node
        attrs["source_center"] = source_center
        return attrs

    def _validate_transfer_eligible_anonymization_status(
        self,
        *,
        anonymization_status: AnonymizationState,
        resource_kind: str,
    ) -> None:
        if anonymization_status not in self._TRANSFER_ELIGIBLE_ANONYMIZATION_STATES:
            raise serializers.ValidationError(
                {
                    "resource_rows": (
                        f"{resource_kind} transfer is only allowed for anonymized "
                        "data that was explicitly validated. "
                        f"Current anonymization_status={anonymization_status.value!r} is not eligible."
                    )
                }
            )

    @staticmethod
    def _resolve_video_anonymization_status(
        video_state_payload: dict[str, object],
    ) -> AnonymizationState:
        if not video_state_payload:
            return AnonymizationState.NOT_STARTED
        if bool(video_state_payload.get("processing_error")):
            return AnonymizationState.FAILED
        if bool(video_state_payload.get("anonymization_validated")):
            return AnonymizationState.VALIDATED
        if bool(video_state_payload.get("sensitive_meta_processed")):
            return AnonymizationState.DONE_PROCESSING_ANONYMIZATION
        if bool(video_state_payload.get("frames_extracted")) and not bool(
            video_state_payload.get("anonymized")
        ):
            return AnonymizationState.PROCESSING_ANONYMIZING
        if bool(video_state_payload.get("was_created")) and not bool(
            video_state_payload.get("frames_extracted")
        ):
            return AnonymizationState.EXTRACTING_FRAMES
        if bool(video_state_payload.get("processing_started")):
            return AnonymizationState.STARTED
        if bool(video_state_payload.get("anonymized")):
            return AnonymizationState.ANONYMIZED
        return AnonymizationState.NOT_STARTED

    @staticmethod
    def _resolve_report_anonymization_status(
        report_state_payload: dict[str, object],
    ) -> AnonymizationState:
        if not report_state_payload:
            return AnonymizationState.NOT_STARTED
        if bool(report_state_payload.get("anonymization_validated")):
            return AnonymizationState.VALIDATED
        if bool(report_state_payload.get("sensitive_meta_processed")):
            return AnonymizationState.DONE_PROCESSING_ANONYMIZATION
        if (
            bool(report_state_payload.get("processing_started"))
            and not bool(report_state_payload.get("processing_error"))
            and not bool(report_state_payload.get("anonymized"))
        ):
            return AnonymizationState.PROCESSING_ANONYMIZING
        if bool(report_state_payload.get("processing_error")):
            return AnonymizationState.FAILED
        if bool(report_state_payload.get("processing_started")):
            return AnonymizationState.STARTED
        if bool(report_state_payload.get("anonymized")):
            return AnonymizationState.ANONYMIZED
        return AnonymizationState.NOT_STARTED

    def _validate_sensitive_meta_linkage(
        self, resource_rows: dict[str, object]
    ) -> None:
        sensitive_meta = cast(
            _SensitiveMetaPayload | None, resource_rows.get("sensitive_meta")
        )
        if sensitive_meta is None or not sensitive_meta:
            return

        has_hashes = bool(
            sensitive_meta.get("patient_hash", "").strip()
            and sensitive_meta.get("examination_hash", "").strip()
        )
        if not has_hashes:
            raise serializers.ValidationError(
                {
                    "resource_rows": (
                        "resource_rows.sensitive_meta must include patient_hash "
                        "and examination_hash; direct identity derivation fields "
                        "are prohibited"
                    )
                }
            )

    def _validate_privacy_boundary(self, resource_rows: dict[str, object]) -> None:
        sensitive_meta = resource_rows.get("sensitive_meta")
        if isinstance(sensitive_meta, dict):
            sensitive_meta_fields = cast(dict[str, object], sensitive_meta)
            direct_identity_fields = sorted(
                set(sensitive_meta_fields).difference(
                    {"patient_hash", "examination_hash"}
                )
            )
            if direct_identity_fields:
                raise serializers.ValidationError(
                    {
                        "resource_rows": (
                            "Direct identity fields are prohibited in hub transfers: "
                            + ", ".join(direct_identity_fields)
                        )
                    }
                )

        raw_pdf_file = resource_rows.get("raw_pdf_file")
        if isinstance(raw_pdf_file, dict) and "text" in raw_pdf_file:
            raise serializers.ValidationError(
                {
                    "resource_rows": (
                        "Raw report text is prohibited in hub transfers; use only "
                        "validated anonymized_text."
                    )
                }
            )


class TransferJobStatusSerializer(serializers.ModelSerializer[TransferJob]):
    source_node_key = serializers.CharField(
        source="source_node.node_key", read_only=True
    )
    target_node_key = serializers.CharField(
        source="target_node.node_key", read_only=True
    )
    source_center_key = serializers.CharField(
        source="source_center.center_key",
        read_only=True,
        allow_null=True,
    )

    class Meta:  # type: ignore[reportIncompatibleVariableOverride]
        model = TransferJob
        fields = [
            "id",
            "transfer_key",
            "source_node_key",
            "target_node_key",
            "source_center_key",
            "resource_kind",
            "resource_hash",
            "transfer_mode",
            "transfer_status",
            "processing_policy",
            "processing_intent",
            "processing_decision",
            "cleanup_policy",
            "cleanup_status",
            "payload_schema_version",
            "status_detail",
            "target_object_id",
            "linked_patient_id",
            "linked_patient_examination_id",
            "case_resolution_status",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields
