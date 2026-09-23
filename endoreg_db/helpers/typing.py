"""Shared Django and loader typing boundaries."""

from collections.abc import Iterable
from pathlib import Path
from typing import TYPE_CHECKING, NotRequired, Protocol, TypeAlias, TypedDict, cast

from django.core.files import File
from django.db.models import Model
from django.db.models.base import ModelBase
from lx_dtypes.models.contracts.video_frame_export import YamlScalar
from rest_framework.permissions import (
    BasePermission,
    OperandHolder,
    SingleOperandHolder,
)

CompositePermissionClass: TypeAlias = (
    type[BasePermission] | OperandHolder | SingleOperandHolder
)


class DjangoModelSaveKwargs(TypedDict, total=False):
    """Keyword arguments accepted by ``django.db.models.Model.save``."""

    force_insert: bool | tuple[ModelBase, ...]
    force_update: bool
    using: str | None
    update_fields: Iterable[str] | None


class ManyToManyAddRelation(Protocol):
    def add(self, *objs: Model) -> None: ...


def m2m_add_relation(manager: object) -> ManyToManyAddRelation:
    return cast(ManyToManyAddRelation, manager)


if TYPE_CHECKING:
    DjangoFile: TypeAlias = File[bytes]
else:
    DjangoFile: TypeAlias = File


class BinaryFieldFileSaver(Protocol):
    """Django's binary save contract, narrowed once at its unparameterized stub."""

    def save(self, name: str, content: DjangoFile, save: bool = True) -> None: ...


YamlValue: TypeAlias = YamlScalar | list[YamlScalar]
LoadModelDataModel: TypeAlias = type[Model]
LoadModelDataDirectory: TypeAlias = str | Path


class YamlEntry(TypedDict):
    fields: dict[str, YamlValue]


class LoadModelDataValidator(Protocol):
    def __call__(
        self,
        fields: dict[str, YamlValue],
        *,
        entry: YamlEntry,
        model: LoadModelDataModel,
    ) -> None: ...


class LoadModelDataMetadata(TypedDict):
    dir: LoadModelDataDirectory
    model: LoadModelDataModel
    foreign_keys: list[str]
    foreign_key_models: list[LoadModelDataModel]
    validators: NotRequired[list[LoadModelDataValidator]]
