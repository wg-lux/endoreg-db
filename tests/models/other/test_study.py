from __future__ import annotations

import pytest
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from rest_framework.test import APIClient

from endoreg_db.models.other.study import Study
from endoreg_db.models.other.study_cohort import StudyCohort

pytestmark = pytest.mark.django_db


def test_study_persists_identity_and_validates_fields() -> None:
    owner = User.objects.create_user(username="study-owner")
    study = Study.objects.create(
        created_by=owner, name="  Study  ", hypothesis="Question"
    )
    identity = study.uuid
    study.refresh_from_db()
    assert study.name == "Study"
    assert study.hypothesis == "Question"
    assert study.uuid == identity
    assert study.created_by_id == owner.pk
    with pytest.raises(ValidationError):
        Study.objects.create(created_by=owner, name="  ")


def test_cohort_persists_in_endoreg_without_lx_annotate() -> None:
    owner = User.objects.create_user(username="cohort-core-owner")
    study = Study.objects.create(created_by=owner, name="Study")
    cohort = StudyCohort.objects.create(
        created_by=owner, study=study, name="Cohort", hypothesis="Question"
    )
    cohort.refresh_from_db()
    assert StudyCohort._meta.app_label == "endoreg_db"
    assert cohort.study_id == study.pk
    assert cohort.filters["limit"] == 100
    with pytest.raises(ValidationError):
        StudyCohort.objects.create(
            created_by=owner,
            study=study,
            name="Invalid",
            hypothesis="Question",
            filters={"limit": 0},
        )


def test_study_api_crud_is_owned_and_requires_authentication() -> None:
    owner = User.objects.create_superuser(
        username="study-api-owner", password="test-only"
    )
    other = User.objects.create_superuser(
        username="study-api-other", password="test-only"
    )
    client = APIClient()
    path = "/endoreg-api/media/studies/"
    assert client.get(path).status_code in {401, 403}
    client.force_authenticate(owner)
    assert client.post(path, {"name": " "}, format="json").status_code == 400
    response = client.post(
        path, {"name": "Study", "hypothesis": "Question"}, format="json"
    )
    assert response.status_code == 201, response.content
    detail = f"{path}{response.json()['id']}/"
    assert client.get(detail).json()["hypothesis"] == "Question"
    assert (
        client.put(
            detail, {"name": "Renamed", "description": "Protocol"}, format="json"
        ).status_code
        == 200
    )
    client.force_authenticate(other)
    assert client.get(path).json() == []
    assert client.delete(detail).status_code == 404
    client.force_authenticate(owner)
    assert client.delete(detail).status_code == 204
    assert not Study.objects.exists()
