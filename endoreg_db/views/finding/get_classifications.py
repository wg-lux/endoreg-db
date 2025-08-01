from endoreg_db.models import Finding


from django.shortcuts import get_object_or_404
from rest_framework.decorators import api_view
from rest_framework.response import Response


@api_view(["GET"])
def get_classifications_for_finding(request, finding_id):
    finding = get_object_or_404(Finding, id=finding_id)
    classifications = finding.get_classifications()

    return Response([{"id": i.id, "name": i.name} for i in classifications])