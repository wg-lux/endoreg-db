from __future__ import annotations

from typing import Protocol, cast, TYPE_CHECKING

from rest_framework import serializers

if TYPE_CHECKING:
    _ModelSerializerMeta = serializers.ModelSerializer.Meta
else:
    _ModelSerializerMeta = object

from endoreg_db.models.hub.upload_job import UploadJob
from endoreg_db.models.media.pdf.report_llm_job import ReportLlmInferenceJob


class _UploadJobLike(Protocol):
    status: str
    error_detail: str | None
    sensitive_meta: object | None
    text: str | None
    anonymized_text: str | None
    source_center: object | None
    source_system: str
    ingest_mode: str
    report_llm_inference_jobs: object


class _ReportLlmInferenceJobQueryLike(Protocol):
    def select_related(
        self, *args: object, **kwargs: object
    ) -> "_ReportLlmInferenceJobQueryLike": ...

    def order_by(
        self, *args: object, **kwargs: object
    ) -> "_ReportLlmInferenceJobQueryLike": ...

    def first(self) -> object | None: ...


class UploadJobStatusSerializer(serializers.ModelSerializer[UploadJob]):
    """
    Read-only serializer for upload job status responses.
    Returns status information for polling endpoints.
    """

    sensitive_meta_id = serializers.IntegerField(
        source="sensitive_meta.id",
        read_only=True,
        allow_null=True,
        help_text="ID of the created SensitiveMeta record (only when anonymized)",
    )

    # Optional helper fields for preview (can be populated by view if needed)
    text = serializers.CharField(read_only=True, required=False, allow_blank=True)
    anonymized_text = serializers.CharField(
        read_only=True, required=False, allow_blank=True
    )
    source_center_key = serializers.CharField(
        source="source_center.center_key",
        read_only=True,
        allow_null=True,
    )
    source_system = serializers.CharField(read_only=True)
    ingest_mode = serializers.CharField(read_only=True)
    report_llm_job = serializers.SerializerMethodField()

    class Meta(_ModelSerializerMeta):
        model = UploadJob  # pyright: ignore[reportAssignmentType]
        fields = [
            "status",
            "error_detail",
            "sensitive_meta_id",
            "id",
            "source_center_key",
            "source_system",
            "ingest_mode",
            "text",
            "anonymized_text",
            "report_llm_job",
        ]
        read_only_fields = fields

    def get_report_llm_job(self, obj: _UploadJobLike) -> dict[str, object] | None:
        job = (
            cast(_ReportLlmInferenceJobQueryLike, obj.report_llm_inference_jobs)
            .select_related("pdf")
            .order_by("-created_at", "-id")
            .first()
        )
        if job is None:
            return None

        from endoreg_db.services.jobs.report_llm_jobs import report_llm_job_payload

        return cast(
            dict[str, object],
            report_llm_job_payload(cast(ReportLlmInferenceJob, job)),
        )

    def to_representation(self, instance: UploadJob) -> dict[str, object]:
        """
        Customize the representation to only include relevant fields based on status.
        """
        data = cast(dict[str, object], super().to_representation(instance))

        # Only include error_detail if status is error
        if str(instance.status) not in {"error", "lost"}:
            data.pop("error_detail", None)

        # Only include sensitive_meta_id if status is anonymized and we have a meta record
        if str(instance.status) != "anonymized" or not instance.sensitive_meta:
            data.pop("sensitive_meta_id", None)

        # Remove empty optional fields
        if not data.get("text"):
            data.pop("text", None)
        if not data.get("anonymized_text"):
            data.pop("anonymized_text", None)
        if data.get("report_llm_job") is None:
            data.pop("report_llm_job", None)

        return data


class UploadCreateResponseSerializer(serializers.Serializer[dict[str, object]]):
    """
    Serializer for the initial upload response.
    Returns upload_id and status_url for polling.
    """

    upload_id = serializers.UUIDField(
        read_only=True, help_text="UUID of the created upload job"
    )

    status_url = serializers.CharField(
        read_only=True, help_text="URL to poll for upload status updates"
    )
