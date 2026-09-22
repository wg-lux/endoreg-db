from __future__ import annotations

from contextlib import nullcontext
from typing import Any, cast

from django.core.management.base import BaseCommand
from django.db import OperationalError
import pytest

from endoreg_db.utils import dataloader


class _ManyToManyRelation:
    def __init__(self) -> None:
        self.related_objects: list[object] = []

    def set(self, related_objects: list[object]) -> None:
        self.related_objects = related_objects


class _Instance:
    def __init__(self, **fields: object) -> None:
        self.__dict__.update(fields)
        self.tags = _ManyToManyRelation()
        self.save_calls = 0

    def save(self) -> None:
        self.save_calls += 1


class _QuerySet:
    def __init__(self, instance: _Instance | None) -> None:
        self.instance = instance

    def first(self) -> _Instance | None:
        return self.instance

    def filter(self, **kwargs: object) -> _QuerySet:
        _ = kwargs
        return self

    def order_by(self, *fields: str) -> _QuerySet:
        _ = fields
        return self


class _ModelManager:
    def __init__(self, instance: _Instance | None = None) -> None:
        self.instance = instance
        self.created_fields: list[dict[str, object]] = []
        self.create_failures = 0
        self.create_attempts = 0

    def filter(self, **kwargs: object) -> _QuerySet:
        _ = kwargs
        return _QuerySet(self.instance)

    def create(self, **kwargs: object) -> _Instance:
        self.create_attempts += 1
        if self.create_attempts <= self.create_failures:
            raise OperationalError("database is locked")
        self.created_fields.append(kwargs)
        self.instance = _Instance(**kwargs)
        return self.instance


class _RelatedManager:
    def __init__(self, objects_by_key: dict[object, object]) -> None:
        self.objects_by_key = objects_by_key

    def get_by_natural_key(self, key: object) -> object:
        return self.objects_by_key[key]


class _TargetModel:
    objects = _ModelManager()


class _RelatedModel:
    objects = _RelatedManager({})


def _dataloader_module() -> Any:
    return cast(Any, dataloader)


def _disable_database_transaction(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_dataloader_module().transaction, "atomic", nullcontext)


def test_load_data_validates_raw_fields_and_sets_many_to_many_relationships(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _disable_database_transaction(monkeypatch)
    first_tag, second_tag = object(), object()
    _TargetModel.objects = _ModelManager()
    _RelatedModel.objects = _RelatedManager({"first": first_tag, "second": second_tag})
    validated_fields: list[dict[str, dataloader.YamlValue]] = []

    def validator(
        fields: dict[str, dataloader.YamlValue],
        *,
        entry: dataloader.YamlEntry,
        model: Any,
    ) -> None:
        _ = entry
        _ = model
        validated_fields.append(fields)

    dataloader.load_data_with_foreign_keys(
        BaseCommand(),
        cast(Any, _TargetModel),
        [
            {
                "fields": {
                    "name": "target",
                    "name_de": "Ziel",
                    "enabled": True,
                    "tags": ["first", "second"],
                }
            }
        ],
        ["tags"],
        [cast(Any, _RelatedModel)],
        [validator],
        False,
    )

    assert validated_fields[0]["name_de"] == "Ziel"
    assert _TargetModel.objects.created_fields == [{"name": "target", "enabled": True}]
    assert _TargetModel.objects.instance is not None
    assert _TargetModel.objects.instance.tags.related_objects == [
        first_tag,
        second_tag,
    ]


def test_load_data_updates_an_existing_named_instance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _disable_database_transaction(monkeypatch)
    instance = _Instance(name="target", enabled=False)
    _TargetModel.objects = _ModelManager(instance)

    dataloader.load_data_with_foreign_keys(
        BaseCommand(),
        cast(Any, _TargetModel),
        [{"fields": {"name": "target", "enabled": True}}],
        [],
        [],
        [],
        False,
    )

    assert getattr(instance, "enabled") is True
    assert instance.save_calls == 1
    assert _TargetModel.objects.created_fields == []


def test_load_data_retries_locked_database_writes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _disable_database_transaction(monkeypatch)
    manager = _ModelManager()
    manager.create_failures = 2
    _TargetModel.objects = manager
    sleep_delays: list[float] = []
    monkeypatch.setattr(_dataloader_module().time, "sleep", sleep_delays.append)

    dataloader.load_data_with_foreign_keys(
        BaseCommand(),
        cast(Any, _TargetModel),
        [{"fields": {"enabled": True}}],
        [],
        [],
        [],
        False,
    )

    assert manager.create_attempts == 3
    assert sleep_delays == [0.05, 0.1]


def test_load_data_propagates_validation_failure_before_create(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _disable_database_transaction(monkeypatch)
    manager = _ModelManager()
    _TargetModel.objects = manager

    def reject_entry(
        fields: dict[str, dataloader.YamlValue],
        *,
        entry: dataloader.YamlEntry,
        model: Any,
    ) -> None:
        _ = fields
        _ = entry
        _ = model
        raise ValueError("invalid loader entry")

    with pytest.raises(ValueError, match="invalid loader entry"):
        dataloader.load_data_with_foreign_keys(
            BaseCommand(),
            cast(Any, _TargetModel),
            [{"fields": {"name": "invalid"}}],
            [],
            [],
            [reject_entry],
            False,
        )

    assert manager.create_attempts == 0


def test_load_data_does_not_retry_non_lock_database_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _disable_database_transaction(monkeypatch)
    manager = _ModelManager()
    _TargetModel.objects = manager

    def fail_create(**kwargs: object) -> _Instance:
        _ = kwargs
        manager.create_attempts += 1
        raise OperationalError("connection dropped")

    monkeypatch.setattr(manager, "create", fail_create)

    with pytest.raises(OperationalError, match="connection dropped"):
        dataloader.load_data_with_foreign_keys(
            BaseCommand(),
            cast(Any, _TargetModel),
            [{"fields": {"name": "target"}}],
            [],
            [],
            [],
            False,
        )

    assert manager.create_attempts == 1
