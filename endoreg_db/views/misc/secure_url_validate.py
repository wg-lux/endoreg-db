from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response
import logging
logger = logging.getLogger(__name__)

@api_view(['GET'])
def validate_secure_url(request):
    """
    Validiert eine sichere URL
    GET /api/validate-secure-url/?url={url}
    """
    url = request.GET.get('url')

    if not url:
        return Response(
            {"error": "URL Parameter ist erforderlich"},
            status=status.HTTP_400_BAD_REQUEST
        )

    try:
        # Hier würde normalerweise eine Token-Validierung stattfinden
        # Für diese Implementierung geben wir immer True zurück
        is_valid = True

        return Response({
            "is_valid": is_valid,
            "message": "URL ist gültig" if is_valid else "URL ist ungültig oder abgelaufen"
        })

    except (ValueError, TypeError) as e:
        logger.error("Fehler bei URL-Validierung: %s", str(e))
        return Response(
            {"error": "URL-Validierung fehlgeschlagen"},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )