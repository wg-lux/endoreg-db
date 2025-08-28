from django.urls import path, include
from rest_framework.routers import DefaultRouter
from endoreg_db.views.requirement.lookup import LookupViewSet
from endoreg_db.views.requirement.evaluate import evaluate_requirements


router = DefaultRouter()
router.register(r"lookup", LookupViewSet, basename="lookup/")

urlpatterns = [
    path("", include(router.urls)),
    path("evaluate-requirements/", evaluate_requirements, name="evaluate-requirements/"),
]
