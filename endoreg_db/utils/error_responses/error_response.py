# endoreg_db/utils/error_responses/error_response.py

from rest_framework.response import Response
from rest_framework import status as drf_status

def error_response(message: str, status_code=drf_status.HTTP_400_BAD_REQUEST):
    """
    Returns a consistent error JSON response.

    Example:
        return error_response("File too large.", drf_status.HTTP_413_REQUEST_ENTITY_TOO_LARGE)

    Output:
        {
            "error": "File too large."
        }

    The format matches your current API, so existing frontend code works unchanged.
    """
    return Response({"error": message}, status=status_code)
