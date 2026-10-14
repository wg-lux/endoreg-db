"""Authenticated, paginated comparisons of the caller's annotation tracks."""

from dataclasses import asdict
from typing import cast

from django.core.paginator import EmptyPage, Paginator
from rest_framework import serializers
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response

from endoreg_db.models.media.video.video_file import VideoFile
from endoreg_db.models.report.patient_examination_report import PatientExaminationReport
from endoreg_db.openapi import OpenApiAPIView
from endoreg_db.services.annotations.annotation_access import (
    resolve_trusted_annotation_principal,
)
from endoreg_db.services.annotations.comparisons import (
    human_annotations,
    video_comparisons,
)
from endoreg_db.views.access_control import (
    filter_center_scoped_queryset,
    filter_video_read_queryset,
)


class FindingComparisonSerializer(serializers.Serializer[dict[str, object]]):
    practitioner = serializers.CharField()
    kind = serializers.ChoiceField(choices=["annotations", "report"])
    report = serializers.IntegerField(allow_null=True)
    matching = serializers.ListField(child=serializers.CharField())
    own = serializers.ListField(child=serializers.CharField())
    other = serializers.ListField(child=serializers.CharField())
    overlap = serializers.FloatField(allow_null=True)


class AnnotationComparisonSerializer(serializers.Serializer[dict[str, object]]):
    practitioner = serializers.CharField()
    shared = serializers.IntegerField()
    agreeing = serializers.IntegerField()
    agreement = serializers.FloatField(allow_null=True)
    intersection = serializers.FloatField(allow_null=True)
    union = serializers.FloatField(allow_null=True)
    overlap = serializers.FloatField(allow_null=True)
    missing = serializers.IntegerField()


class VideoComparisonSerializer(serializers.Serializer[dict[str, object]]):
    video = serializers.IntegerField()
    annotations = AnnotationComparisonSerializer(many=True)
    findings = FindingComparisonSerializer(many=True)
    conflicts = serializers.IntegerField()
    unmapped = serializers.ListField(child=serializers.CharField())
    unavailable = serializers.ListField(child=serializers.CharField())


class ComparisonPageSerializer(serializers.Serializer[dict[str, object]]):
    practitioner = serializers.CharField()
    count = serializers.IntegerField()
    page = serializers.IntegerField()
    pages = serializers.IntegerField()
    results = VideoComparisonSerializer(many=True)


class AnnotationComparisonsView(OpenApiAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = ComparisonPageSerializer

    def get(self, request: Request) -> Response:
        principal = resolve_trusted_annotation_principal(request, None)
        if set(request.query_params) - {"page"}:
            raise ValidationError(
                "Only the page parameter is supported; comparisons use the signed-in practitioner."
            )
        try:
            page = int(request.query_params.get("page", "1"))
        except ValueError as exc:
            raise ValidationError("page must be a positive integer") from exc
        if page < 1:
            raise ValidationError("page must be a positive integer")
        own_videos = (
            human_annotations().filter(annotator=principal).values("frame__video_id")
        )
        videos = filter_video_read_queryset(
            queryset=VideoFile.objects.filter(pk__in=own_videos).select_related(
                "examination", "sensitive_meta__pseudo_examination"
            ),
            user=request.user,
        ).order_by("pk")
        paginator = Paginator(videos, 5)
        try:
            selected = paginator.page(page)
        except EmptyPage as exc:
            raise NotFound("Comparison page does not exist") from exc
        reports = filter_center_scoped_queryset(
            queryset=PatientExaminationReport.objects.all(),
            user=request.user,
            center_field="patient_examination__patient__center_id",
        )
        return Response(
            {
                "practitioner": principal,
                "count": paginator.count,
                "page": page,
                "pages": paginator.num_pages,
                "results": [
                    asdict(
                        video_comparisons(
                            cast(VideoFile, video), principal=principal, reports=reports
                        )
                    )
                    for video in selected
                ],
            }
        )
