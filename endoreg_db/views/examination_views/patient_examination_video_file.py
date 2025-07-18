from rest_framework.viewsets import ModelViewSet
from endoreg_db.models import (
    Examination,
    FindingClassificationChoice,
    FindingClassification
)
from ...serializers.optimized_examination_serializers import ExaminationSerializer as OptimizedExaminationSerializer

from rest_framework.response import Response
from django.shortcuts import get_object_or_404
from rest_framework.decorators import api_view