from django.http import HttpRequest
from django.middleware.csrf import get_token
from rest_framework.response import Response


# New view to return the CSRF token in JSON format
def csrf_token_view(request: HttpRequest) -> Response:
    token = get_token(request)
    return Response({"csrf_token": token})
