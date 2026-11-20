from typing import cast, Any


def model_pk(instance: object) -> int:
    return int(cast(Any, instance).pk)


def optional_model_pk(instance: object | None) -> int | None:
    if instance is None:
        return None
    pk = cast(Any, instance).pk
    return int(pk) if pk is not None else None


def model_fk(instance: object, field_name: str) -> int:
    return int(getattr(cast(Any, instance), f"{field_name}_id"))
