# endoreg_db/views/misc/secure_url_validate.py

from urllib.parse import urlparse

from django.core.exceptions import ValidationError
from django.core.validators import URLValidator

from rest_framework import status
from rest_framework.decorators import api_view, throttle_classes
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle

import logging
logger = logging.getLogger(__name__)

# Accept only http/https; adjust if you later need custom schemes
_url_validator = URLValidator(schemes=["http", "https"])
_MAX_URL_LENGTH = 2048  # pragmatic upper bound to avoid abuse

@api_view(['GET'])
@throttle_classes([ScopedRateThrottle])  # 429s will be returned automatically if exceeded
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

    # Basic length guardrails
    if len(url) > _MAX_URL_LENGTH:
        return Response(
            {"error": "URL ist zu lang"},
            status=status.HTTP_400_BAD_REQUEST
        )

    # Syntactic + scheme validation
    try:
        _url_validator(url)
    except ValidationError:
        return Response(
            {"error": "Ungültiges URL-Format (erlaubt sind http/https)"},
            status=status.HTTP_400_BAD_REQUEST
        )

    # Extra cheap sanity checks (netloc must exist, no data/file/javascript, etc.)
    parsed = urlparse(url)
    if not parsed.netloc:
        return Response(
            {"error": "Ungültige URL: Hostname fehlt"},
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

# Assign a throttle scope so we can configure a specific rate in settings
validate_secure_url.throttle_scope = "secure-url-validate"

# for update got to base_settings.py