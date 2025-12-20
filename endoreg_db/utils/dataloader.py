import os
from datetime import UTC, datetime

import yaml
from django.core.exceptions import ObjectDoesNotExist
from django.db import OperationalError, transaction

from endoreg_db.utils.paths import STORAGE_DIR

_WARNING_LOG_PATH = None


def _get_warning_log_path():
    """Return the path used for warning logs, creating it on first access."""
    global _WARNING_LOG_PATH
    if _WARNING_LOG_PATH is None:
        log_dir = STORAGE_DIR / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        _WARNING_LOG_PATH = log_dir / f"dataloader_warnings_{timestamp}.log"
    return _WARNING_LOG_PATH


def _record_warning(command, message, verbose, context):
    """Write a warning to stdout (when verbose) and append it to the log file."""
    prefix = f"[{context}] " if context else ""
    full_message = f"{prefix}{message}"

    if verbose:
        command.stdout.write(command.style.WARNING(full_message))

    log_path = _get_warning_log_path()
    with open(log_path, "a", encoding="utf-8") as log_file:
        log_file.write(f"{datetime.now(UTC).isoformat()}Z {full_message}\n")


def load_model_data_from_yaml(command, model_name, metadata, verbose):
    """
    Load model data from YAML files.

    Args:
        command: Command object for stdout writing.
        model_name: Name of the model being loaded.
        metadata: Metadata including directory and foreign key information.
        verbose: Boolean indicating whether to print verbose output.
    """
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

    _files = [f for f in os.listdir(dir_path) if f.endswith(".yaml")]
    # sort
    _files.sort()
    for file in _files:
        with open(os.path.join(dir_path, file), "r", encoding="utf-8") as file:
            yaml_data = yaml.safe_load(file)

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
    command,
    model,
    yaml_data,
    foreign_keys,
    foreign_key_models,
    validators,
    verbose,
    log_context=None,
):
    """
    Load YAML data into Django model instances with FK and M2M support.

    Processes each YAML entry to create or update a model instance. For each entry, the
    function extracts field data and uses the presence of a 'name' field to decide whether
    to update an existing instance or create a new one. Foreign key fields listed in
    foreign_keys are handled by retrieving related objects via natural keys. When a field
    contains a list, it is treated as a many-to-many relationship and the corresponding
    objects are set after the instance is saved. Missing or unresolved foreign keys trigger
    warnings if verbose output is enabled.

    Parameters:
        model: The Django model class representing the data.
        yaml_data: A list of dictionaries representing YAML entries.
        foreign_keys: A list of foreign key field names to process from each entry.
        foreign_key_models: The corresponding Django model classes for each foreign key.
        validators: A sequence of callables invoked before persisting each entry. Each
            validator receives a shallow copy of the entry's field dictionary along with
            the original entry and model for context.
        verbose: If True, prints detailed output and warnings during processing.
        log_context: Label that identifies the source dataset inside the warning log.
    """
    context_label = log_context or getattr(model, "__name__", "dataloader")

    for entry in yaml_data:
        raw_fields = entry.get("fields", {})

        for validator in validators:
            validator(dict(raw_fields), entry=entry, model=model)

        fields = dict(raw_fields)
        name = fields.pop("name", None)

        if getattr(model, "_meta", None) and model._meta.model_name == "requirement":
            requirement_types = fields.get("requirement_types", [])

            if not requirement_types:
                raise ValueError(
                    f"Requirement '{name}' must define at least one requirement_types entry."
                )

        ####################
        # TODO REMOVE AFTER TRANSLATION SUPPORT IS ADDED
        SKIP_NAMES = [
            "name_de",  # German name, not used
            "name_en",  # English name, not used
            "description_de",  # German description
            "description_en",  # English description
        ]

        # Remove fields that are not needed
        for skip_name in SKIP_NAMES:
            if skip_name in fields:
                fields.pop(skip_name)
        # ########################

        m2m_relationships = {}  # Store many-to-many relationships
        # print(entry)

        # Handle foreign keys and many-to-many relationships
        for fk_field, fk_model in zip(foreign_keys, foreign_key_models):
            # Skip fields that are not in the data
            if fk_field not in fields:
                continue

            target_keys = fields.pop(fk_field, None)

            # Ensure the foreign key exists
            if target_keys is None:
                _record_warning(
                    command,
                    f"Foreign key {fk_field} not found in fields",
                    verbose,
                    context_label,
                )
                continue  # Skip if no foreign key provided

            # Process many-to-many fields or foreign keys
            if isinstance(target_keys, list):  # Assume many-to-many relationship
                related_objects = []
                for key in target_keys:
                    try:
                        obj = fk_model.objects.get_by_natural_key(key)
                    except ObjectDoesNotExist:
                        _record_warning(
                            command,
                            f"{fk_model.__name__} with key {key} not found",
                            verbose,
                            context_label,
                        )
                        continue
                    related_objects.append(obj)
                m2m_relationships[fk_field] = related_objects
            else:  # Single foreign key relationship
                if model.__name__ == "ModelMeta" and fk_field == "labelset":
                    labelset_version = fields.pop("labelset_version", None)

                    if isinstance(target_keys, (tuple, list)):
                        labelset_name = target_keys[0] if target_keys else None
                        if len(target_keys) > 1 and labelset_version in (None, ""):
                            labelset_version = target_keys[1]
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

                    queryset = fk_model.objects.filter(name=labelset_name)
                    if labelset_version not in (None, "", -1):
                        try:
                            version_value = int(labelset_version)
                        except (TypeError, ValueError):
                            version_value = labelset_version
                        queryset = queryset.filter(version=version_value)

                    obj = queryset.order_by("-version").first()
                    if obj is None:
                        _record_warning(
                            command,
                            f"LabelSet '{labelset_name}' (version={labelset_version}) not found",
                            verbose,
                            context_label,
                        )
                        continue
                    fields[fk_field] = obj
                else:
                    try:
                        obj = fk_model.objects.get_by_natural_key(target_keys)
                    except ObjectDoesNotExist:
                        _record_warning(
                            command,
                            f"{fk_model.__name__} with key {target_keys} not found",
                            verbose,
                            context_label,
                        )
                        continue
                    fields[fk_field] = obj

        # Create or update the main object (avoid update_or_create to prevent SQLite locks)
        version_value = fields.get("version")

        def _save_instance():
            if name is None:
                # Try to find an existing object by all provided fields
                obj = model.objects.filter(**fields).first()
                if obj is None:
                    obj = model.objects.create(**fields)
                    created = True
                else:
                    created = False
            else:
                lookup_kwargs = {"name": name}
                if model.__name__ == "LabelSet" and version_value is not None:
                    lookup_kwargs["version"] = version_value

                obj = model.objects.filter(**lookup_kwargs).first()
                if obj is None:
                    obj = model.objects.create(name=name, **fields)
                    created = True
                else:
                    # Update fields
                    for k, v in fields.items():
                        setattr(obj, k, v)
                    obj.save()
                    created = False
            return obj, created

        try:
            # Attempt save inside a transaction for consistency
            with transaction.atomic():
                obj, created = _save_instance()
        except OperationalError:
            # Retry once on SQLite lock
            obj, created = _save_instance()

        if created and verbose:
            command.stdout.write(
                command.style.SUCCESS(f"Created {model.__name__} {name}")
            )
        elif verbose:
            pass

        # Set many-to-many relationships
        for field_name, related_objs in m2m_relationships.items():
            if related_objs:  # Only set if there are objects to set
                getattr(obj, field_name).set(related_objs)
                if verbose:
                    command.stdout.write(
                        command.style.SUCCESS(
                            f"Set {len(related_objs)} {field_name} for {model.__name__} {name}"
                        )
                    )
