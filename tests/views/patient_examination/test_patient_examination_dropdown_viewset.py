from __future__ import annotations

from collections.abc import Sequence
from typing import Any, cast

import pytest
from rest_framework.test import APIClient

from endoreg_db.models import Examination


@pytest.mark.django_db
def test_examinations_dropdown_returns_serialized_examinations(
    api_client: APIClient,
) -> None:
    examination = Examination.objects.create(name="dropdown_contract_examination")

    response = api_client.get("/api/patient-examinations/examinations_dropdown/")

    assert response.status_code == 200
    rows = cast(Sequence[dict[str, object]], response.data)
    assert {
        "id": int(cast(Any, examination).pk),
        "name": "dropdown_contract_examination",
        "display_name": "dropdown_contract_examination",
    } in rows
