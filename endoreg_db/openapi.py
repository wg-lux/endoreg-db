"""DRF schema metadata conventions for Endoreg API endpoints."""

from __future__ import annotations

from collections.abc import Callable
from typing import TypeVar, cast

from django.http import HttpResponse, StreamingHttpResponse
from rest_framework import serializers
from rest_framework.decorators import api_view as drf_api_view
from rest_framework.views import APIView


class OpenApiUnspecifiedObjectSerializer(serializers.Serializer[dict[str, object]]):
    """Schema-only marker for a legacy JSON object whose fields are not declared yet.

    New endpoints must provide a concrete request and response serializer. This
    marker lets Django REST Framework endpoints that still validate their body
    manually remain visible in OpenAPI without claiming a field-level contract.
    """


class OpenApiAPIView(APIView):
    """Base class that supplies the mandatory fallback schema metadata."""

    serializer_class = OpenApiUnspecifiedObjectSerializer


ViewFunction = TypeVar(
    "ViewFunction", bound=Callable[..., HttpResponse | StreamingHttpResponse]
)


def api_view(http_method_names: list[str]) -> Callable[[ViewFunction], ViewFunction]:
    """Attach fallback serializer metadata to a function-based DRF endpoint."""

    def decorate(view: ViewFunction) -> ViewFunction:
        decorated = drf_api_view(http_method_names)(view)
        setattr(decorated.cls, "serializer_class", OpenApiUnspecifiedObjectSerializer)
        return cast(ViewFunction, decorated)

    return decorate
