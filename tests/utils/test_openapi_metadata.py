from __future__ import annotations

from typing import Protocol, cast

from django.http import HttpRequest
from django.urls import URLPattern, URLResolver
from rest_framework.response import Response
from rest_framework.views import APIView

from endoreg_db.openapi import (
    OpenApiAPIView,
    OpenApiUnspecifiedObjectSerializer,
    api_view,
)
from endoreg_db.urls import urlpatterns


class _DecoratedApiView(Protocol):
    cls: type[OpenApiAPIView]


def test_class_based_views_receive_the_standard_openapi_serializer() -> None:
    assert OpenApiAPIView.serializer_class is OpenApiUnspecifiedObjectSerializer


def test_function_based_views_receive_the_standard_openapi_serializer() -> None:
    @api_view(["GET"])
    def endpoint(request: HttpRequest) -> Response:
        del request
        return Response({})

    decorated_endpoint = cast(_DecoratedApiView, endpoint)
    assert decorated_endpoint.cls.serializer_class is OpenApiUnspecifiedObjectSerializer


def _flatten_urlpatterns(
    patterns: list[URLPattern | URLResolver],
) -> list[URLPattern]:
    flattened: list[URLPattern] = []
    for pattern in patterns:
        if isinstance(pattern, URLResolver):
            flattened.extend(_flatten_urlpatterns(list(pattern.url_patterns)))
        else:
            flattened.append(pattern)
    return flattened


def test_every_url_mounted_drf_view_exposes_serializer_metadata() -> None:
    documented_view_count = 0
    for pattern in _flatten_urlpatterns(urlpatterns):
        callback = pattern.callback
        view_class = getattr(callback, "cls", None) or getattr(
            callback, "view_class", None
        )
        if not isinstance(view_class, type) or not issubclass(view_class, APIView):
            continue
        if not view_class.__module__.startswith("endoreg_db."):
            continue
        documented_view_count += 1
        assert hasattr(view_class, "serializer_class"), str(pattern.pattern)

    assert documented_view_count > 0
