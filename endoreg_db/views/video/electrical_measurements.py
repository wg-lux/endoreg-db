"""Read-only, center-scoped electrosurgical measurement endpoints for one video.

Wire contract (snake_case only) is documented in
``docs/electrosurgical_video_alignment.md``. Responses never contain raw
samples: frames carry per-channel mean/minimum/maximum from the lx_dtypes
``electrical_frame_window_v1`` alignment, or an explicit gap status.
"""

from __future__ import annotations

from lx_dtypes.models.contracts.electrical_video_alignment import (
    ELECTRICAL_FRAME_WINDOW_ALGORITHM,
)
from lx_dtypes.models.contracts.json_types import JsonObject
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response

from endoreg_db.models.media.video.electrosurgical_activation import (
    VideoElectrosurgicalActivationLog,
)
from endoreg_db.models.media.video.electrosurgical_measurement import (
    VideoElectrosurgicalMeasurement,
)
from endoreg_db.models.media.video.video_file import VideoFile
from endoreg_db.openapi import OpenApiAPIView as APIView
from endoreg_db.schemas.electrosurgical_activation import ActivationImportMetadata
from endoreg_db.schemas.electrosurgical_measurement import (
    parse_aggregation_profile,
    parse_channel_descriptors,
)
from endoreg_db.services.electrical.activation_alignment import (
    MAX_ACTIVATIONS_PER_REQUEST,
    align_activation_export,
)
from endoreg_db.services.electrical.video_alignment import (
    MAX_ALIGNMENT_FRAMES_PER_REQUEST,
    ElectricalAlignmentRefusal,
    ElectricalAlignmentUnavailable,
    align_measurement_frames,
    describe_alignment_eligibility,
)
from endoreg_db.views.access_control import CenterScopedVideoPermission

_CLIENT_ERRORS = frozenset(
    {
        ElectricalAlignmentRefusal.UNKNOWN_CHANNEL,
        ElectricalAlignmentRefusal.INVALID_RANGE,
    }
)


class ElectricalFramesQuery(BaseModel):
    """Query parameters; integers arrive as strings and are parsed exactly once."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    generation_content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    first_frame: int = Field(default=0, ge=0)
    frame_count: int = Field(
        default=MAX_ALIGNMENT_FRAMES_PER_REQUEST,
        ge=1,
        le=MAX_ALIGNMENT_FRAMES_PER_REQUEST,
    )
    channel_id: tuple[str, ...] = Field(default=(), max_length=32)


def _measurement_summary(measurement: VideoElectrosurgicalMeasurement) -> JsonObject:
    return {
        "id": measurement.pk,
        "source_sha256": measurement.source_sha256,
        "schema_version": measurement.schema_version,
        "parser_id": measurement.parser_id,
        "parser_version": measurement.parser_version,
        "session_id": measurement.session_id,
        "electrical_clock_id": measurement.electrical_clock_id,
        "time_base_num": measurement.time_base_num,
        "time_base_den": measurement.time_base_den,
        "sample_count": measurement.sample_count,
        "channels": [
            channel.model_dump(mode="json")
            for channel in parse_channel_descriptors(measurement.channels).channels
        ],
        "profile": parse_aggregation_profile(
            measurement.aggregation_profile
        ).model_dump(mode="json"),
        "imported_at": measurement.imported_at.isoformat(),
    }


def _refusal(exc: ElectricalAlignmentUnavailable) -> Response:
    status = 400 if exc.code in _CLIENT_ERRORS else 409
    return Response({"code": exc.code.value, "error": exc.message}, status=status)


class VideoElectricalMeasurementsView(APIView):
    """List imported series and whether the current generation can be aligned."""

    permission_classes = [IsAuthenticated, CenterScopedVideoPermission]

    def get(self, request: Request, pk: int) -> Response:
        del request
        video = VideoFile.objects.filter(pk=pk).first()
        if video is None:
            return Response(
                {"code": "video_not_found", "error": "Video not found"}, status=404
            )
        eligibility = describe_alignment_eligibility(video)
        measurements = VideoElectrosurgicalMeasurement.objects.filter(
            video_id=video.pk
        ).order_by("imported_at", "pk")
        return Response(
            {
                "video_id": video.pk,
                "generation_content_hash": video.processed_video_hash or None,
                "timeline_version": "pts_v1",
                "algorithm": ELECTRICAL_FRAME_WINDOW_ALGORITHM,
                "electrical_matching_eligible": eligibility.eligible,
                "ineligibility_code": (
                    eligibility.refusal.value if eligibility.refusal else None
                ),
                "ineligibility_reason": eligibility.message,
                "total_frame_count": eligibility.total_frame_count,
                "max_frames_per_request": MAX_ALIGNMENT_FRAMES_PER_REQUEST,
                "measurements": [_measurement_summary(m) for m in measurements],
                "activation_logs": [
                    {
                        "id": record.pk,
                        "source_sha256": record.source_sha256,
                        "source_format": metadata.source_format.value,
                        "source_timezone": metadata.source_timezone,
                        "activation_count": metadata.activation_count,
                        "imported_at": record.imported_at.isoformat(),
                    }
                    for record in VideoElectrosurgicalActivationLog.objects.filter(
                        video=video
                    ).order_by("imported_at", "pk")
                    for metadata in [
                        ActivationImportMetadata.model_validate(record.metadata)
                    ]
                ],
                "max_activations_per_request": MAX_ACTIVATIONS_PER_REQUEST,
            }
        )


class VideoElectricalAlignedFramesView(APIView):
    """Bounded frame windows of one series on the published generation's PTS."""

    permission_classes = [IsAuthenticated, CenterScopedVideoPermission]

    def get(self, request: Request, pk: int, measurement_id: int) -> Response:
        params = request.query_params
        unknown = set(params.keys()) - set(ElectricalFramesQuery.model_fields)
        if unknown:
            return Response(
                {"code": "invalid_request", "error": "Unknown query parameters"},
                status=400,
            )
        try:
            query = ElectricalFramesQuery.model_validate(
                {
                    **{
                        key: params[key]
                        for key in (
                            "generation_content_hash",
                            "first_frame",
                            "frame_count",
                        )
                        if key in params
                    },
                    "channel_id": tuple(params.getlist("channel_id")),
                }
            )
        except ValidationError:
            return Response(
                {
                    "code": "invalid_request",
                    "error": (
                        "generation_content_hash is required; first_frame >= 0 and "
                        f"1 <= frame_count <= {MAX_ALIGNMENT_FRAMES_PER_REQUEST}"
                    ),
                },
                status=400,
            )
        video = VideoFile.objects.filter(pk=pk).first()
        if video is None:
            return Response(
                {"code": "video_not_found", "error": "Video not found"}, status=404
            )
        measurement = VideoElectrosurgicalMeasurement.objects.filter(
            pk=measurement_id, video_id=video.pk
        ).first()
        if measurement is None:
            return Response(
                {"code": "measurement_not_found", "error": "Measurement not found"},
                status=404,
            )
        try:
            page = align_measurement_frames(
                video,
                measurement,
                expected_generation_content_hash=query.generation_content_hash,
                first_frame=query.first_frame,
                frame_count=query.frame_count,
                channel_ids=query.channel_id,
            )
        except ElectricalAlignmentUnavailable as exc:
            return _refusal(exc)
        alignment = page.alignment.model_dump(mode="json", exclude={"frames"})
        frames = [
            {
                **frame.model_dump(mode="json"),
                "presentation_time_seconds": seconds,
            }
            for frame, seconds in zip(
                page.alignment.frames, page.presentation_time_seconds, strict=True
            )
        ]
        return Response(
            {
                "video_id": video.pk,
                "measurement_id": measurement.pk,
                "timeline_version": "pts_v1",
                "alignment": alignment,
                "range": {
                    "first_frame": page.first_frame,
                    "frame_count": page.frame_count,
                    "total_frame_count": page.total_frame_count,
                    "next_first_frame": page.next_first_frame,
                },
                "frames": frames,
            }
        )


class ElectricalActivationsQuery(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    generation_content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    first_activation: int = Field(default=0, ge=0)
    activation_count: int = Field(
        default=MAX_ACTIVATIONS_PER_REQUEST, ge=1, le=MAX_ACTIVATIONS_PER_REQUEST
    )


class VideoElectricalActivationsView(APIView):
    """Read-only activation summaries, exposing values only inside verified coverage."""

    permission_classes = [IsAuthenticated, CenterScopedVideoPermission]

    def get(self, request: Request, pk: int, activation_log_id: int) -> Response:
        try:
            query = ElectricalActivationsQuery.model_validate(
                dict(request.query_params.items())
            )
        except ValidationError:
            return Response(
                {
                    "code": "invalid_request",
                    "error": "Invalid activation range or generation",
                },
                status=400,
            )
        video = VideoFile.objects.filter(pk=pk).first()
        record = VideoElectrosurgicalActivationLog.objects.filter(
            pk=activation_log_id, video_id=pk
        ).first()
        if video is None or record is None:
            return Response(
                {
                    "code": "activation_log_not_found",
                    "error": "Activation log not found",
                },
                status=404,
            )
        try:
            page = align_activation_export(
                video,
                record,
                expected_generation_content_hash=query.generation_content_hash,
                first_activation=query.first_activation,
                activation_count=query.activation_count,
            )
        except ElectricalAlignmentUnavailable as exc:
            return _refusal(exc)
        except (ValueError, OSError):
            return Response(
                {
                    "code": "source_integrity_failed",
                    "error": "Activation source failed verification",
                },
                status=409,
            )
        return Response(page.model_dump(mode="json"))
