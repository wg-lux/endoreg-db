from typing import Protocol, TYPE_CHECKING

from rest_framework import serializers

if TYPE_CHECKING:
    _ModelSerializerMeta = serializers.ModelSerializer.Meta
else:
    _ModelSerializerMeta = object

from endoreg_db.models.media.pdf.pdf_processing_history import PdfProcessingHistory
from endoreg_db.utils.media_urls import (
    build_absolute_media_url,
    build_pdf_stream_path,
)


class _PdfProcessingHistoryLike(Protocol):
    pdf_id: int
    operation: object
    actor_user_id: int | None
    actor_username: str
    actor_email: str

    def get_operation_display(self) -> object: ...


class PdfProcessingHistorySerializer(serializers.ModelSerializer[PdfProcessingHistory]):
    revision_id = serializers.IntegerField(source="id", read_only=True)
    file_id = serializers.IntegerField(source="pdf_id", read_only=True)
    operation_display = serializers.SerializerMethodField()
    user = serializers.SerializerMethodField()
    processed_stream_url = serializers.SerializerMethodField()

    class Meta(_ModelSerializerMeta):
        model = PdfProcessingHistory  # pyright: ignore[reportAssignmentType]
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

    def get_operation_display(self, obj: _PdfProcessingHistoryLike) -> str:
        display = getattr(obj, "get_operation_display", None)
        result = display() if callable(display) else obj.operation
        return str(result)

    def get_user(self, obj: _PdfProcessingHistoryLike) -> dict[str, object]:
        return {
            "id": obj.actor_user_id,
            "username": obj.actor_username or "",
            "email": obj.actor_email or "",
        }

    def get_processed_stream_url(self, obj: _PdfProcessingHistoryLike) -> str:
        context = self.context
        request = context.get("request") if context else None
        return build_absolute_media_url(
            request,
            build_pdf_stream_path(obj.pdf_id, file_type="processed"),
        )
