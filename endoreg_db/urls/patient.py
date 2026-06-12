from collections.abc import Callable
from typing import Protocol, cast

from django.http.response import HttpResponseBase
from endoreg_db.views import (
    GenderViewSet,
    CenterViewSet,
    PatientViewSet,
)
from rest_framework.routers import DefaultRouter
from django.urls import path, include
from rest_framework.viewsets import ViewSetMixin


class _ViewSetAsViewLike(Protocol):
    def as_view(self, actions: dict[str, str]) -> Callable[..., HttpResponseBase]: ...


router = DefaultRouter()
router.register(r"patients", PatientViewSet)
router.register(r"centers", CenterViewSet)
router.register(r"genders", GenderViewSet)

urlpatterns = [
    path("", include(router.urls)),
    path(
        "check_pe_exist/<int:pk>/",
        cast(_ViewSetAsViewLike, cast(ViewSetMixin, PatientViewSet)).as_view(
            {"get": "check_pe_exist"}
        ),
        name="check_pe_exist",
    ),
]
