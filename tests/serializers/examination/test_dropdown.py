from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol, cast

from endoreg_db.models import Examination
from endoreg_db.serializers.examination import ExaminationDropdownSerializer


class _SerializerData(Protocol):
    @property
    def data(self) -> Mapping[str, object]: ...


def test_examination_dropdown_serializer_uses_canonical_name() -> None:
    examination = Examination(id=17, name="colonoscopy")

    data = cast(
        _SerializerData,
        ExaminationDropdownSerializer(instance=examination),
    ).data

    assert data == {
        "id": 17,
        "name": "colonoscopy",
        "display_name": "colonoscopy",
    }
