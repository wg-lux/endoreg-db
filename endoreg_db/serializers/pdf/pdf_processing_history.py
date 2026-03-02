from collections.abc import Mapping

from rest_framework import serializers

from endoreg_db.models import PdfProcessingHistory


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
        relative_url = f"/api/media/pdfs/{obj.pdf_id}/stream/?type=processed"
        context = self.context if isinstance(self.context, Mapping) else None
        request = context.get("request") if context else None
        if request:
            return request.build_absolute_uri(relative_url)
        return relative_url
