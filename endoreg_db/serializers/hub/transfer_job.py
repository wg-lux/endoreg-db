from __future__ import annotations

from rest_framework import serializers

from endoreg_db.models.administration.center.center import Center
from endoreg_db.models.hub.network_node import NetworkNode
from endoreg_db.models.hub.transfer_job import TransferJob
from endoreg_db.models.state.anonymization import AnonymizationState


class TransferJobCreateSerializer(serializers.Serializer):
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

    _TRANSFER_ELIGIBLE_ANONYMIZATION_STATES = {
        AnonymizationState.ANONYMIZED,
        AnonymizationState.DONE_PROCESSING_ANONYMIZATION,
        AnonymizationState.VALIDATED,
    }

    def validate_source_node_key(self, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise serializers.ValidationError("source_node_key is required")
        if not NetworkNode.objects.filter(node_key=normalized, is_active=True).exists():
            raise serializers.ValidationError(
                f"Unknown active source_node_key: {normalized}"
            )
        return normalized

    def validate(self, attrs: dict) -> dict:
        transfer_mode = attrs["transfer_mode"]

        if transfer_mode in {
            TransferJob.TransferMode.METADATA_AND_RAW_MEDIA,
            TransferJob.TransferMode.METADATA_RAW_AND_PROCESSED_MEDIA,
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
        target_node_key = (attrs.get("target_node_key") or "").strip()
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

        source_center_key = (attrs.get("source_center_key") or "").strip()
        source_center = None
        if source_center_key:
            source_center = Center.objects.filter(center_key=source_center_key).first()
            if source_center is None:
                raise serializers.ValidationError(
                    {"source_center_key": f"Unknown center_key: {source_center_key}"}
                )
        elif source_node.owning_center_id is not None:
            source_center = source_node.owning_center

        resource_rows = attrs.get("resource_rows") or {}
        if attrs["resource_kind"] == TransferJob.ResourceKind.VIDEO:
            video_file = resource_rows.get("video_file")
            if not isinstance(video_file, dict):
                raise serializers.ValidationError(
                    {
                        "resource_rows": (
                            "resource_rows.video_file is required for video transfers"
                        )
                    }
                )
            video_hash = str(video_file.get("video_hash", "")).strip()
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
                TransferJob.TransferMode.METADATA_AND_PROCESSED_MEDIA,
                TransferJob.TransferMode.METADATA_RAW_AND_PROCESSED_MEDIA,
            }:
                processed_video_hash = str(
                    video_file.get("processed_video_hash", "")
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
            video_state_payload = resource_rows.get("video_state") or {}
            anonymization_status = self._resolve_video_anonymization_status(
                video_state_payload
            )
            self._validate_transfer_eligible_anonymization_status(
                anonymization_status=anonymization_status,
                resource_kind="video",
            )
        elif attrs["resource_kind"] == TransferJob.ResourceKind.REPORT:
            report_file = resource_rows.get("raw_pdf_file")
            if not isinstance(report_file, dict):
                raise serializers.ValidationError(
                    {
                        "resource_rows": (
                            "resource_rows.raw_pdf_file is required for report transfers"
                        )
                    }
                )
            pdf_hash = str(report_file.get("pdf_hash", "")).strip()
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
            report_state_payload = resource_rows.get("raw_pdf_state") or {}
            anonymization_status = self._resolve_report_anonymization_status(
                report_state_payload
            )
            self._validate_transfer_eligible_anonymization_status(
                anonymization_status=anonymization_status,
                resource_kind="report",
            )

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
                        f"{resource_kind} transfer is only allowed for anonymized data. "
                        f"Current anonymization_status={anonymization_status.value!r} is not eligible."
                    )
                }
            )

    @staticmethod
    def _resolve_video_anonymization_status(
        video_state_payload: object,
    ) -> AnonymizationState:
        if not isinstance(video_state_payload, dict):
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
        report_state_payload: object,
    ) -> AnonymizationState:
        if not isinstance(report_state_payload, dict):
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

    def _validate_sensitive_meta_linkage(self, resource_rows: dict) -> None:
        sensitive_meta = resource_rows.get("sensitive_meta")
        if not isinstance(sensitive_meta, dict) or not sensitive_meta:
            return

        has_hashes = bool(
            str(sensitive_meta.get("patient_hash", "")).strip()
            and str(sensitive_meta.get("examination_hash", "")).strip()
        )
        has_derivation_fields = all(
            str(sensitive_meta.get(field, "")).strip()
            for field in (
                "patient_first_name",
                "patient_last_name",
                "patient_dob",
                "examination_date",
            )
        )

        if not has_hashes and not has_derivation_fields:
            raise serializers.ValidationError(
                {
                    "resource_rows": (
                        "resource_rows.sensitive_meta must include either "
                        "patient_hash and examination_hash, or "
                        "patient_first_name, patient_last_name, patient_dob, "
                        "and examination_date"
                    )
                }
            )


class TransferJobStatusSerializer(serializers.ModelSerializer):
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

    class Meta:
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
