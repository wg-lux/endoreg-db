"""Center-scoped recovery available to every authenticated user in that center."""

from lx_dtypes.models.contracts.video_provenance import RecordingTimeRange
from pydantic import StrictBool, ValidationError
from rest_framework import serializers
from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response

from endoreg_db.models.media.video.video_file import VideoFile
from endoreg_db.services.hub.video_provenance_recovery import (
    ActiveVideoProcessing,
    recover_video_provenance,
)
from endoreg_db.services.media.operation_gate import MediaOperationDeferred
from endoreg_db.views.access_control import CenterScopedVideoPermission


class ProvenanceRecoveryRequest(RecordingTimeRange):
    acknowledge_unresolved: StrictBool
    dry_run: StrictBool = True


class ProvenanceRecoverySerializer(serializers.Serializer[dict[str, object]]):
    acknowledge_unresolved = serializers.BooleanField(write_only=True)
    dry_run = serializers.BooleanField(default=True, write_only=True)
    recording_started_at = serializers.CharField(required=False, allow_null=True)
    recording_ended_at = serializers.CharField(required=False, allow_null=True)
    video_id = serializers.IntegerField(read_only=True)
    status = serializers.ChoiceField(choices=["preview", "recovered"], read_only=True)
    mapping_status = serializers.ChoiceField(choices=["unresolved"], read_only=True)
    electrical_matching_eligible = serializers.BooleanField(read_only=True)
    original_timestamps_available = serializers.BooleanField(read_only=True)
    processed_content_hash = serializers.CharField(read_only=True)


class VideoProvenanceRecoveryView(APIView):
    permission_classes = [IsAuthenticated, CenterScopedVideoPermission]
    serializer_class = ProvenanceRecoverySerializer

    def post(self, request: Request, pk: int) -> Response:
        return _recover(request, pk)


def _recover(request: Request, pk: int) -> Response:
    try:
        options = ProvenanceRecoveryRequest.model_validate(request.data)
    except ValidationError:
        return Response({"error": "Invalid provenance recovery request"}, status=400)
    if not options.acknowledge_unresolved:
        return Response(
            {
                "error": "Explicit acknowledgment of unresolved frame mapping is required"
            },
            status=400,
        )
    actor_id = request.user.pk
    if not isinstance(actor_id, int):
        return Response({"error": "A persisted user account is required"}, status=403)
    try:
        receipt = recover_video_provenance(
            pk,
            actor_id=actor_id,
            dry_run=options.dry_run,
            recording_started_at=options.recording_started_at,
            recording_ended_at=options.recording_ended_at,
        )
    except VideoFile.DoesNotExist:
        return Response({"error": "Video not found"}, status=404)
    except MediaOperationDeferred:
        return Response(
            {
                "code": "active_media_operation",
                "error": "Video is in use. Stop playback and retry after active work completes.",
            },
            status=409,
        )
    except ActiveVideoProcessing as exc:
        return Response({"code": "active_processing", "error": str(exc)}, status=409)
    except ValueError as exc:
        return Response(
            {"code": "recovery_precondition_failed", "error": str(exc)}, status=409
        )
    return Response(
        {
            "video_id": pk,
            "status": "preview" if options.dry_run else "recovered",
            "mapping_status": receipt.mapping_status,
            "electrical_matching_eligible": False,
            "original_timestamps_available": receipt.original_timeline is not None,
            "processed_content_hash": receipt.processed_timeline.content_hash,
            "recording_started_at": receipt.recording_started_at,
            "recording_ended_at": receipt.recording_ended_at,
        }
    )
