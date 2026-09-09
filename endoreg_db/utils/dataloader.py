from __future__ import annotations

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

from endoreg_db.data import DATA_DIR as BASE_DATA_DIR
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
_TRANSLATION_FIELDS = (
    "name_de",
    "name_en",
    "description_de",
    "description_en",
)
_BASE_DATA_ROOT = BASE_DATA_DIR.resolve()


class DataLoaderSourceError(ValueError):
    """Raised when YAML input escapes the packaged base-data boundary."""


def _approved_yaml_directory(directory: str | Path) -> Path:
    source = Path(directory)
    if source.is_symlink():
        raise DataLoaderSourceError("YAML source directories must not be symlinks")

    resolved_source = source.resolve()
    try:
        resolved_source.relative_to(_BASE_DATA_ROOT.resolve())
    except ValueError as exc:
        raise DataLoaderSourceError(
            "YAML source directory must resolve within the packaged base-data root"
        ) from exc
    return resolved_source


def _approved_yaml_file(path: Path) -> Path:
    if path.is_symlink():
        raise DataLoaderSourceError("YAML source files must not be symlinks")

    resolved_path = path.resolve()
    try:
        resolved_path.relative_to(_BASE_DATA_ROOT.resolve())
    except ValueError as exc:
        raise DataLoaderSourceError(
            "YAML source file must resolve within the packaged base-data root"
        ) from exc
    if not resolved_path.is_file():
        raise DataLoaderSourceError("YAML source must be a regular file")
    return resolved_path


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

    source_directory = _approved_yaml_directory(dir_path)
    if not source_directory.is_dir():
        _record_warning(
            command,
            f"Skipping load because YAML data directory is missing: {dir_path}",
            verbose,
            model_name or model.__name__,
        )
        return

    yaml_files = sorted(
        path for path in source_directory.iterdir() if path.suffix == ".yaml"
    )
    for yaml_path in yaml_files:
        approved_path = _approved_yaml_file(yaml_path)
        with approved_path.open("r", encoding="utf-8") as fh:
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
        name, fields = _prepare_entry_fields(entry, model, validators)
        relationships = _resolve_relationships(
            command,
            model,
            fields,
            foreign_keys,
            foreign_key_models,
            verbose,
            context_label,
        )
        obj = _save_with_lock_retry(model, name, fields)
        _set_many_to_many_relationships(obj, relationships)


def _prepare_entry_fields(
    entry: YamlEntry,
    model: DataLoaderModel,
    validators: list[DataLoaderValidator],
) -> tuple[str | None, dict[str, Any]]:
    raw_fields = dict(entry.get("fields", {}))
    for validator in validators:
        validator(dict(raw_fields), entry=entry, model=model)

    fields: dict[str, Any] = dict(raw_fields)
    name = cast(str | None, fields.pop("name", None))
    for translation_field in _TRANSLATION_FIELDS:
        fields.pop(translation_field, None)
    return name, fields


def _resolve_relationships(
    command: BaseCommand,
    model: DataLoaderModel,
    fields: dict[str, Any],
    foreign_keys: list[str],
    foreign_key_models: list[DataLoaderModel],
    verbose: bool,
    context_label: str,
) -> dict[str, list[object]]:
    relationships: dict[str, list[object]] = {}
    for field_name, related_model in zip(foreign_keys, foreign_key_models):
        _resolve_relationship_field(
            command,
            model,
            related_model,
            field_name,
            fields,
            relationships,
            verbose,
            context_label,
        )
    return relationships


def _resolve_relationship_field(
    command: BaseCommand,
    model: DataLoaderModel,
    related_model: DataLoaderModel,
    field_name: str,
    fields: dict[str, Any],
    relationships: dict[str, list[object]],
    verbose: bool,
    context_label: str,
) -> None:
    if field_name not in fields:
        return

    target_keys = fields.pop(field_name)
    if target_keys is None:
        _record_warning(
            command,
            f"Foreign key {field_name} not found in fields",
            verbose,
            context_label,
        )
        return
    if isinstance(target_keys, list):
        relationships[field_name] = _resolve_many_relationships(
            command,
            related_model,
            cast(list[Any], target_keys),
            verbose,
            context_label,
        )
        return

    related_object = _resolve_single_relationship(
        command,
        model,
        related_model,
        field_name,
        target_keys,
        fields,
        verbose,
        context_label,
    )
    if related_object is not None:
        fields[field_name] = related_object


def _resolve_many_relationships(
    command: BaseCommand,
    related_model: DataLoaderModel,
    target_keys: list[Any],
    verbose: bool,
    context_label: str,
) -> list[object]:
    related_objects: list[object] = []
    manager = cast(_NaturalKeyManagerLike, related_model.objects)
    for key in target_keys:
        try:
            related_object = manager.get_by_natural_key(key)
        except ObjectDoesNotExist:
            _record_missing_related_object(
                command,
                related_model,
                key,
                verbose,
                context_label,
            )
            continue
        related_objects.append(related_object)
    return related_objects


def _resolve_single_relationship(
    command: BaseCommand,
    model: DataLoaderModel,
    related_model: DataLoaderModel,
    field_name: str,
    target_key: YamlScalar,
    fields: dict[str, Any],
    verbose: bool,
    context_label: str,
) -> object | None:
    if model.__name__ == "ModelMeta" and field_name == "labelset":
        return _resolve_model_meta_labelset(
            command,
            related_model,
            target_key,
            fields,
            verbose,
            context_label,
        )

    try:
        return cast(_NaturalKeyManagerLike, related_model.objects).get_by_natural_key(
            target_key
        )
    except ObjectDoesNotExist:
        _record_missing_related_object(
            command,
            related_model,
            target_key,
            verbose,
            context_label,
        )
        return None


def _resolve_model_meta_labelset(
    command: BaseCommand,
    labelset_model: DataLoaderModel,
    labelset_name: YamlScalar,
    fields: dict[str, Any],
    verbose: bool,
    context_label: str,
) -> object | None:
    labelset_version = fields.pop("labelset_version", None)
    if not labelset_name:
        _record_warning(
            command,
            "LabelSet name missing for ModelMeta entry",
            verbose,
            context_label,
        )
        return None

    queryset = cast(_ModelManagerLike, labelset_model.objects).filter(
        name=labelset_name
    )
    if labelset_version not in (None, "", -1):
        queryset = queryset.filter(
            version=_normalize_labelset_version(labelset_version)
        )

    related_object = queryset.order_by("-version").first()
    if related_object is None:
        _record_warning(
            command,
            f"LabelSet '{labelset_name}' (version={labelset_version}) not found",
            verbose,
            context_label,
        )
    return related_object


def _normalize_labelset_version(labelset_version: Any) -> Any:
    try:
        if isinstance(labelset_version, list):
            raise ValueError
        return int(labelset_version)
    except (TypeError, ValueError):
        return cast(YamlScalar, labelset_version)


def _record_missing_related_object(
    command: BaseCommand,
    related_model: DataLoaderModel,
    target_key: object,
    verbose: bool,
    context_label: str,
) -> None:
    _record_warning(
        command,
        f"{related_model.__name__} with key {target_key} not found",
        verbose,
        context_label,
    )


def _save_with_lock_retry(
    model: DataLoaderModel,
    name: str | None,
    fields: dict[str, Any],
) -> object:
    max_attempts = 4
    for attempt in range(1, max_attempts + 1):
        try:
            with transaction.atomic():
                return _save_instance(model, name, fields)
        except OperationalError as exc:
            if "database is locked" not in str(exc).lower() or attempt == max_attempts:
                raise
            time.sleep(0.05 * attempt)
    raise AssertionError("database lock retry loop ended without a result")


def _save_instance(
    model: DataLoaderModel,
    name: str | None,
    fields: dict[str, Any],
) -> object:
    manager = cast(_ModelManagerLike, model.objects)
    if name is None:
        return _find_or_create_unnamed_instance(manager, fields)
    return _create_or_update_named_instance(manager, model, name, fields)


def _find_or_create_unnamed_instance(
    manager: _ModelManagerLike,
    fields: dict[str, Any],
) -> object:
    instance = manager.filter(**fields).first()
    return instance if instance is not None else manager.create(**fields)


def _create_or_update_named_instance(
    manager: _ModelManagerLike,
    model: DataLoaderModel,
    name: str,
    fields: dict[str, Any],
) -> object:
    lookup_kwargs: dict[str, Any] = {"name": name}
    version_value = fields.get("version")
    if model.__name__ == "LabelSet" and version_value is not None:
        lookup_kwargs["version"] = version_value

    instance = manager.filter(**lookup_kwargs).first()
    if instance is None:
        return manager.create(name=name, **fields)

    for field_name, value in fields.items():
        setattr(instance, field_name, value)
    cast(_ModelLikeWithSave, instance).save()
    return instance


def _set_many_to_many_relationships(
    instance: object,
    relationships: dict[str, list[object]],
) -> None:
    for field_name, related_objects in relationships.items():
        if related_objects:
            getattr(instance, field_name).set(related_objects)
