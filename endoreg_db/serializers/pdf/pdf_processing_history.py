from collections.abc import Mapping

from rest_framework import serializers

from endoreg_db.models import PdfProcessingHistory
from endoreg_db.utils.web.media_urls import (
    build_absolute_media_url,
    build_pdf_stream_path,
)


class PdfProcessingHistorySerializer(serializers.ModelSerializer):
    revision_id = serializers.IntegerField(source="id", read_only=True)
    file_id = serializers.IntegerField(source="pdf_id", read_only=True)
    operation_display = serializers.SerializerMethodField()
    user = serializers.SerializerMethodField()
    processed_stream_url = serializers.SerializerMethodField()

    class Meta:
        model = PdfProcessingHistory
        fields = [
            "revision_id",
            "file_id",
            "operation",
            "operation_display",
            "source_type",
            "note",
            "redaction_manifest",
            "client_source_sha256",
            "source_sha256",
            "processed_file_name",
            "processed_stream_url",
            "user",
            "created_at",
        ]
        read_only_fields = fields

    def get_operation_display(self, obj) -> str:
        display = getattr(obj, "get_operation_display", None)
        result = display() if callable(display) else obj.operation
        return str(result)

    def get_user(self, obj) -> dict[str, object]:
        return {
            "id": obj.actor_user_id,
            "username": obj.actor_username or "",
            "email": obj.actor_email or "",
        }

    def get_processed_stream_url(self, obj) -> str:
        context = self.context if isinstance(self.context, Mapping) else None
        request = context.get("request") if context else None
        return build_absolute_media_url(
            request,
            build_pdf_stream_path(obj.pdf_id, file_type="processed"),
        )
