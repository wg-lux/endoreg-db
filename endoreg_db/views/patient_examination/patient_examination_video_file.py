from rest_framework.viewsets import ModelViewSet
from endoreg_db.models import (
    Examination,
    FindingClassificationChoice,
    FindingClassification
)
from ...serializers.examination import ExaminationSerializer

from rest_framework.response import Response
from django.shortcuts import get_object_or_404
from rest_framework.decorators import api_view

__all__ = [
    
]