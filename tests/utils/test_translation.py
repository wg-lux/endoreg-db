from __future__ import annotations

from dataclasses import dataclass, field

from endoreg_db.utils.translation import build_multilingual_response


def _empty_choices() -> list["_Source"]:
    return []


@dataclass
class _Source:
    id: int
    name: str
    name_de: str
    name_en: str
    description: str
    description_de: str
    description_en: str
    choices: list["_Source"] = field(default_factory=_empty_choices)

    def get_choices(self) -> list["_Source"]:
        return self.choices


@dataclass
class _RequiredSource(_Source):
    required: bool = True


def _source(
    source_id: int,
    choices: list[_Source] | None = None,
) -> _Source:
    return _Source(
        id=source_id,
        name=f"name-{source_id}",
        name_de=f"name-de-{source_id}",
        name_en=f"name-en-{source_id}",
        description=f"description-{source_id}",
        description_de=f"description-de-{source_id}",
        description_en=f"description-en-{source_id}",
        choices=choices or [],
    )


def _required_source(
    source_id: int,
    choices: list[_Source],
) -> _RequiredSource:
    return _RequiredSource(
        id=source_id,
        name=f"name-{source_id}",
        name_de=f"name-de-{source_id}",
        name_en=f"name-en-{source_id}",
        description=f"description-{source_id}",
        description_de=f"description-de-{source_id}",
        description_en=f"description-en-{source_id}",
        choices=choices,
        required=False,
    )


def test_build_multilingual_response_returns_only_base_fields_by_default() -> None:
    source = _source(7)

    assert build_multilingual_response(source) == {
        "id": 7,
        "name": "name-7",
        "name_de": "name-de-7",
        "name_en": "name-en-7",
        "description": "description-7",
        "description_de": "description-de-7",
        "description_en": "description-en-7",
    }


def test_build_multilingual_response_adds_required_choices_and_classification() -> None:
    choice = _source(9)
    source = _required_source(7, [choice])

    result = build_multilingual_response(
        source,
        include_choices=True,
        classification_id=42,
    )

    assert result.get("required") is False
    assert result.get("choices") == [
        {
            "id": 9,
            "name": "name-9",
            "name_de": "name-de-9",
            "name_en": "name-en-9",
            "description": "description-9",
            "description_de": "description-de-9",
            "description_en": "description-en-9",
            "classification_id": 42,
        }
    ]
    assert "classification_id" not in result
