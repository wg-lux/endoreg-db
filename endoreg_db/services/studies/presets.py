"""Atomic host projection of explicitly selected terminology presets."""

from typing import cast

from django.db import models
from lx_dtypes.models.interface.KnowledgeBase import KnowledgeBase
from lx_dtypes.serialization import parse_str_list

from endoreg_db.models.administration.center.center import Center
from endoreg_db.models.label.label import Label
from endoreg_db.models.label.label_set import LabelSet
from endoreg_db.models.label.label_type import LabelType
from endoreg_db.models.medical.examination.examination import Examination
from endoreg_db.models.medical.examination.examination_type import ExaminationType
from endoreg_db.models.medical.examination.examination_indication import (
    ExaminationIndication,
)
from endoreg_db.models.medical.finding.finding import Finding
from endoreg_db.models.other.gender import Gender
from endoreg_db.services.centers.employees import import_center_employees
from endoreg_db.services.reference_data.catalog import reference_catalog_transaction
from endoreg_db.helpers.typing import ReferenceRelation


def _reuse_or_create[T: models.Model](
    model: type[T],
    name: str,
    defaults: dict[str, object],
    *,
    version: int | None = None,
    relations: dict[str, list[models.Model]] | None = None,
) -> T:
    """Reuse equivalent definitions; never reinterpret rows referenced by patients."""
    identity: dict[str, object] = {"name": name}
    if version is not None:
        identity["version"] = version
    row, created = model._base_manager.get_or_create(**identity, defaults=defaults)
    if not created:
        for field, expected in defaults.items():
            if getattr(row, field) != expected:
                raise ValueError(
                    f"Preset conflicts with existing {model.__name__} {name}: {field}"
                )
    for field, related in (relations or {}).items():
        manager = cast(ReferenceRelation, getattr(row, field))
        if created:
            manager.set(related)
        elif set(manager.all().values_list("pk", flat=True)) != {
            item.pk for item in related
        }:
            raise ValueError(
                f"Preset conflicts with existing {model.__name__} {name}: {field}"
            )
    return row


def _required_rows[T: models.Model](model: type[T], names: str | list[str]) -> list[T]:
    return [model._default_manager.get(name=name) for name in parse_str_list(names)]


def import_study_preset(knowledge_base: KnowledgeBase) -> None:
    """Keep row identities and fail atomically on missing clinical dependencies.

    PostgreSQL serializes
    cooperating writers across hosts using a transaction-scoped advisory lock.
    """
    presets = list(knowledge_base.study_preset.values())
    centers = [record for preset in presets for record in preset.centers]
    genders = [record for preset in presets for record in preset.genders]
    label_types = [record for preset in presets for record in preset.label_types]
    labels = [record for preset in presets for record in preset.labels]
    label_sets = [record for preset in presets for record in preset.label_sets]
    for names in (
        [record.name for record in centers],
        [record.name for record in genders],
        [record.name for record in label_types],
        [record.name for record in labels],
    ):
        if len(names) != len(set(names)):
            raise ValueError("Preset definitions contain duplicate natural identities")
    label_set_keys = [(record.name, record.version) for record in label_sets]
    if len(label_set_keys) != len(set(label_set_keys)):
        raise ValueError("Preset definitions contain duplicate label set identities")
    with reference_catalog_transaction():
        for definition in centers:
            matches = Center.objects.filter(center_key=definition.name)
            center = matches.get() if matches.exists() else None
            if center is None:
                legacy = Center.objects.filter(name=definition.name)
                if legacy.exists():
                    center = legacy.get()
                    if center.center_key != definition.name:
                        raise ValueError(
                            "Center preset conflicts with an existing immutable center_key"
                        )
                else:
                    Center.objects.create(
                        name=definition.name, center_key=definition.name
                    )
        for gender in genders:
            _reuse_or_create(
                Gender,
                gender.name,
                {
                    "abbreviation": gender.abbreviation,
                    "description": gender.description,
                },
            )
        for label_type in label_types:
            _reuse_or_create(
                LabelType, label_type.name, {"description": label_type.description}
            )
        for label in labels:
            label_type = (
                LabelType.objects.get(name=label.label_type)
                if label.label_type is not None
                else None
            )
            _reuse_or_create(
                Label,
                label.name,
                {"label_type": label_type, "description": label.description},
            )
        for label_set in label_sets:
            set_labels = _required_rows(Label, label_set.labels)
            _reuse_or_create(
                LabelSet,
                label_set.name,
                {"description": label_set.description},
                version=label_set.version,
                relations={"labels": list(set_labels)},
            )
        for definition in knowledge_base.examination_type.values():
            if not definition.name or len(definition.name) > 100:
                raise ValueError(
                    "Examination type names must contain 1 to 100 characters"
                )
            _reuse_or_create(ExaminationType, definition.name, {})
        for definition in knowledge_base.examination.values():
            if not definition.name or len(definition.name) > 100:
                raise ValueError("Examination names must contain 1 to 100 characters")
            findings = _required_rows(Finding, definition.findings)
            indications = _required_rows(ExaminationIndication, definition.indications)
            examination_types = _required_rows(
                ExaminationType, definition.examination_types
            )
            _reuse_or_create(
                Examination,
                definition.name,
                {"description": definition.description},
                relations={
                    "findings": list(findings),
                    "indications": list(indications),
                    "examination_types": list(examination_types),
                },
            )
        import_center_employees(knowledge_base)
