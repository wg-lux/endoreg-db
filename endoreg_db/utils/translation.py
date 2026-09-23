from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, cast

from lx_dtypes.models.contracts.translation import MultilingualResponseData


class _MultilingualSource(Protocol):
    id: int
    name: str
    name_de: str
    name_en: str
    description: str
    description_de: str
    description_en: str

    def get_choices(self) -> Sequence["_MultilingualSource"]: ...


class _RequiredSource(_MultilingualSource, Protocol):
    required: bool


def build_multilingual_response(
    obj: _MultilingualSource,
    include_choices: bool = False,
    classification_id: int | None = None,
) -> MultilingualResponseData:
    """
    Helper to build a multilingual response dict for an object.
    If include_choices is True, adds a 'choices' key with multilingual dicts for each choice.
    If classification_id is given, adds 'classification_id' to each choice.
    """
    data: MultilingualResponseData = {
        "id": obj.id,
        "name": obj.name,
        "name_de": obj.name_de,
        "name_en": obj.name_en,
        "description": obj.description,
        "description_de": obj.description_de,
        "description_en": obj.description_en,
    }
    if hasattr(obj, "required"):
        required_obj = cast(_RequiredSource, obj)
        data["required"] = required_obj.required
    if include_choices:
        data["choices"] = [
            build_multilingual_response(
                choice,
                include_choices=False,
                classification_id=classification_id or obj.id,
            )
            for choice in obj.get_choices()
        ]
        for choice_dict in data["choices"]:
            choice_dict["classification_id"] = classification_id or obj.id
    if classification_id is not None and not include_choices:
        data["classification_id"] = classification_id
    return data
