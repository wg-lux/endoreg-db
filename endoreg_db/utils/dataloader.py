from __future__ import annotations

import os
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, TypeAlias, TypedDict, cast
import yaml
from django.core.management.base import BaseCommand
from django.core.exceptions import ObjectDoesNotExist
from django.db import OperationalError, connection, transaction
from django.db import models
from django.db.models.options import Options

from endoreg_db.utils.file_operations import (
    atomic_write_file,
    ensure_directory,
)
from endoreg_db.utils.paths import LOG_DIR

YamlNull: TypeAlias = None
YamlScalar: TypeAlias = str | int | float | bool | YamlNull
YamlValue: TypeAlias = YamlScalar | list[YamlScalar]


class YamlEntry(TypedDict):
    fields: dict[str, YamlValue]


if TYPE_CHECKING:
    DataLoaderModel: TypeAlias = "_ModelLike"

    class DataLoaderValidator(Protocol):
        def __call__(
            self,
            fields: dict[str, YamlValue],
            *,
            entry: YamlEntry,
            model: DataLoaderModel,
        ) -> None: ...

    class LoadModelDataMetadata(TypedDict):
        dir: str | Path
        model: DataLoaderModel
        foreign_keys: list[str]
        foreign_key_models: list[DataLoaderModel]
        validators: list[DataLoaderValidator]


class _CommandLike(Protocol):
    stdout: Any
    style: Any


class _ModelManagerLike(Protocol):
    def filter(self, **kwargs: Any) -> "_QuerySetLike": ...
    def create(self, **kwargs: Any) -> models.Model: ...


class _NaturalKeyManagerLike(Protocol):
    def get_by_natural_key(self, key: Any) -> models.Model: ...


class _ModelLike(Protocol):
    __name__: str
    # Fixed error: Added type argument to generic Options class
    _meta: Options[Any]
    objects: Any


class _QuerySetLike(Protocol):
    def first(self) -> models.Model | None: ...
    def filter(self, **kwargs: Any) -> "_QuerySetLike": ...
    def order_by(self, *fields: str) -> "_QuerySetLike": ...


class _ModelLikeWithSave(_ModelLike, Protocol):
    def save(self) -> None: ...


_warning_log_path: Path | None = None


def _get_warning_log_path() -> Path:
    """Return the path used for warning logs, creating it on first access."""
    global _warning_log_path
    if _warning_log_path is None:
        ensure_directory(LOG_DIR)
        timestamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        _warning_log_path = LOG_DIR / f"dataloader_warnings_{timestamp}.log"
    return _warning_log_path


def _record_warning(
    command: _CommandLike,
    message: str,
    verbose: bool,
    context: str | None,
) -> None:
    """Write a warning to stdout (when verbose) and append it to the log file."""
    prefix = f"[{context}] " if context else ""
    full_message = f"{prefix}{message}"

    if verbose:
        command.stdout.write(command.style.WARNING(full_message))

    log_path = _get_warning_log_path()
    existing = log_path.read_bytes() if log_path.exists() else b""
    atomic_write_file(
        destination=log_path,
        content=[
            existing,
            f"{datetime.now(UTC).isoformat()}Z {full_message}\n".encode("utf-8"),
        ],
    )


def load_model_data_from_yaml(
    command: BaseCommand,
    model_name: str,
    metadata: LoadModelDataMetadata,
    verbose: bool = False,
) -> None:
    """Load model data from YAML files."""
    if verbose:
        command.stdout.write(f"Start loading {model_name}")

    warning_log_path = _get_warning_log_path()
    if verbose:
        command.stdout.write(f"Warning log file: {warning_log_path}")

    model = metadata["model"]
    dir_path = metadata["dir"]
    foreign_keys = metadata["foreign_keys"]
    foreign_key_models = metadata["foreign_key_models"]
    validators = metadata.get("validators", [])

    existing_tables: set[str] = set(connection.introspection.table_names())
    required_tables: set[str] = {
        cast(str, getattr(candidate, "_meta").db_table)
        for candidate in [model, *foreign_key_models]
    }
    missing_tables = sorted(required_tables - existing_tables)
    if missing_tables:
        _record_warning(
            command,
            f"Skipping load because database tables are not available yet: {', '.join(missing_tables)}",
            verbose,
            model_name or model.__name__,
        )
        return

    if not os.path.isdir(dir_path):
        _record_warning(
            command,
            f"Skipping load because YAML data directory is missing: {dir_path}",
            verbose,
            model_name or model.__name__,
        )
        return

    _files: list[str] = [f for f in os.listdir(dir_path) if f.endswith(".yaml")]
    _files.sort()
    for filename in _files:
        with open(os.path.join(dir_path, filename), "r", encoding="utf-8") as fh:
            yaml_data = cast(list[YamlEntry], yaml.safe_load(fh) or [])

        load_data_with_foreign_keys(
            command,
            model,
            yaml_data,
            foreign_keys,
            foreign_key_models,
            validators,
            verbose,
            log_context=model_name or model.__name__,
        )


def load_data_with_foreign_keys(
    command: BaseCommand,
    model: DataLoaderModel,
    yaml_data: list[YamlEntry],
    foreign_keys: list[str],
    foreign_key_models: list[DataLoaderModel],
    validators: list[DataLoaderValidator],
    verbose: bool = False,
    log_context: str | None = None,
) -> None:
    """Load YAML data into Django model instances with FK and M2M support."""
    context_label = log_context or getattr(model, "__name__", "dataloader")

    for entry in yaml_data:
        raw_fields: dict[str, YamlValue] = dict(entry.get("fields", {}))

        for validator in validators:
            validator(dict(raw_fields), entry=entry, model=model)

        fields: dict[str, Any] = dict(raw_fields)
        name = cast(str | None, fields.pop("name", None))

        ####################
        # TODO REMOVE AFTER TRANSLATION SUPPORT IS ADDED
        SKIP_NAMES = [
            "name_de",
            "name_en",
            "description_de",
            "description_en",
        ]

        for skip_name in SKIP_NAMES:
            if skip_name in fields:
                fields.pop(skip_name)
        ########################

        m2m_relationships: dict[str, list[object]] = {}

        for fk_field, fk_model in zip(foreign_keys, foreign_key_models):
            if fk_field not in fields:
                continue

            target_keys: Any = fields.pop(fk_field, None)

            if target_keys is None:
                _record_warning(
                    command,
                    f"Foreign key {fk_field} not found in fields",
                    verbose,
                    context_label,
                )
                continue

            if isinstance(target_keys, list):
                related_objects: list[object] = []
                # Fixed error: Cast target_keys to eliminate Unknown loop variable types
                for key in cast(list[Any], target_keys):
                    try:
                        obj_fk = cast(
                            _NaturalKeyManagerLike, fk_model.objects
                        ).get_by_natural_key(key)
                    except ObjectDoesNotExist:
                        _record_warning(
                            command,
                            f"{fk_model.__name__} with key {key} not found",
                            verbose,
                            context_label,
                        )
                        continue
                    related_objects.append(obj_fk)
                m2m_relationships[fk_field] = related_objects
            else:
                if model.__name__ == "ModelMeta" and fk_field == "labelset":
                    # Fixed error: Explicitly typed labelset_version to Any
                    labelset_version: Any = fields.pop("labelset_version", None)

                    if isinstance(target_keys, list):
                        target_seq = cast(list[YamlScalar], target_keys)
                        labelset_name: Any = target_seq[0] if target_seq else None
                        if len(target_seq) > 1 and labelset_version in (None, ""):
                            labelset_version = target_seq[1]
                    else:
                        labelset_name = target_keys

                    if not labelset_name:
                        _record_warning(
                            command,
                            "LabelSet name missing for ModelMeta entry",
                            verbose,
                            context_label,
                        )
                        continue

                    queryset = cast(_ModelManagerLike, fk_model.objects).filter(
                        name=labelset_name
                    )
                    if labelset_version not in (None, "", -1):
                        # Fixed error: Explicitly type version_value
                        try:
                            if isinstance(labelset_version, list):
                                raise ValueError
                            version_value = int(labelset_version)
                        except (TypeError, ValueError):
                            version_value = cast(YamlScalar, labelset_version)
                        queryset = queryset.filter(version=version_value)

                    obj_fk = queryset.order_by("-version").first()
                    if obj_fk is None:
                        _record_warning(
                            command,
                            f"LabelSet '{labelset_name}' (version={labelset_version}) not found",
                            verbose,
                            context_label,
                        )
                        continue
                    fields[fk_field] = obj_fk
                else:
                    try:
                        obj_fk = cast(
                            _NaturalKeyManagerLike, fk_model.objects
                        ).get_by_natural_key(target_keys)
                    except ObjectDoesNotExist:
                        _record_warning(
                            command,
                            f"{fk_model.__name__} with key {target_keys} not found",
                            verbose,
                            context_label,
                        )
                        continue
                    fields[fk_field] = obj_fk

        version_value = fields.get("version")

        def _save_instance() -> tuple[object, bool]:
            if name is None:
                instance = (
                    cast(_ModelManagerLike, model.objects).filter(**fields).first()
                )
                if instance is None:
                    instance = cast(_ModelManagerLike, model.objects).create(**fields)
                    is_created = True
                else:
                    is_created = False
            else:
                # Fixed error: Explicitly typed dict[str, Any] to allow integer version updates later
                lookup_kwargs: dict[str, Any] = {"name": name}
                if model.__name__ == "LabelSet" and version_value is not None:
                    lookup_kwargs["version"] = version_value

                instance = (
                    cast(_ModelManagerLike, model.objects)
                    .filter(**lookup_kwargs)
                    .first()
                )
                if instance is None:
                    instance = cast(_ModelManagerLike, model.objects).create(
                        name=name, **fields
                    )
                    is_created = True
                else:
                    for k, v in fields.items():
                        setattr(instance, k, v)
                    cast(_ModelLikeWithSave, instance).save()
                    is_created = False
            return instance, is_created

        obj: Any = None
        max_attempts = 4
        for attempt in range(1, max_attempts + 1):
            try:
                with transaction.atomic():
                    obj, _ = _save_instance()
                break
            except OperationalError as exc:
                if (
                    "database is locked" not in str(exc).lower()
                    or attempt == max_attempts
                ):
                    raise
                time.sleep(0.05 * attempt)

        for field_name, related_objs in m2m_relationships.items():
            if related_objs:
                getattr(obj, field_name).set(related_objs)
