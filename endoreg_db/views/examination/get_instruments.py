# from endoreg_db.models import (
#     Examination,
#     FindingClassificationChoice,
# )
from rest_framework.response import Response
# from django.shortcuts import get_object_or_404
from rest_framework.decorators import api_view


@api_view(["GET"])
def get_instruments_for_examination(request, exam_id):
    # Placeholder if you plan to link instruments to exams
    """
    Returns an empty list of instruments for the specified examination.
    
    This placeholder endpoint is intended for future implementation of instrument retrieval linked to an examination.
    """
    return Response([])