from __future__ import annotations

from typing import TYPE_CHECKING

from rest_framework import serializers

from endoreg_db.models.hub.quarantine_item import QuarantineItem

if TYPE_CHECKING:
    _ModelSerializerMeta = serializers.ModelSerializer.Meta
else:
    _ModelSerializerMeta = object


class QuarantineItemSerializer(serializers.ModelSerializer[QuarantineItem]):
    reviewed_by_username = serializers.CharField(
        source="reviewed_by.username",
        read_only=True,
        allow_null=True,
    )
    source_upload_job_id = serializers.UUIDField(
        source="source_upload_job.id",
        read_only=True,
        allow_null=True,
    )

    class Meta(_ModelSerializerMeta):
        model = QuarantineItem  # pyright: ignore[reportAssignmentType]
        fields = [
            "id",
            "status",
            "path",
            "relative_path",
            "original_filename",
            "size_bytes",
            "file_mtime_ns",
            "quarantined_at",
            "last_seen_at",
            "source_upload_job_id",
            "metadata",
            "decision_reason",
            "reviewed_by_username",
            "reviewed_at",
            "delete_eligible_at",
            "deleted_at",
            "error_detail",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields


class QuarantineSyncRequestSerializer(serializers.Serializer[dict[str, object]]):
    older_than_days = serializers.IntegerField(
        required=False,
        min_value=0,
        default=30,
    )


class QuarantineDecisionRequestSerializer(serializers.Serializer[dict[str, object]]):
    decision_reason = serializers.CharField(
        allow_blank=False,
        trim_whitespace=True,
    )
    delete_after_days = serializers.IntegerField(
        required=False,
        min_value=0,
        default=0,
    )


class QuarantineReapRequestSerializer(serializers.Serializer[dict[str, object]]):
    older_than_days = serializers.IntegerField(
        required=False,
        min_value=0,
        default=30,
    )
    dry_run = serializers.BooleanField(
        required=False,
        default=True,
    )
