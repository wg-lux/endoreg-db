from django.urls import path
from endoreg_db.views.requirement.evaluate import evaluate_requirements

urlpatterns = [
    path(
        "evaluate-requirements/", evaluate_requirements, name="evaluate-requirements/"
    ),
]
