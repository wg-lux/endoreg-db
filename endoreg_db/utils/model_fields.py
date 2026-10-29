from datetime import datetime


def model_value(instance: object, field_name: str) -> object:
    return getattr(instance, field_name)


def model_text(instance: object, field_name: str) -> str:
    return str(model_value(instance, field_name) or "")


def model_optional_text(instance: object, field_name: str) -> str | None:
    value = model_value(instance, field_name)
    return str(value) if value not in {None, ""} else None


def model_bool(instance: object, field_name: str) -> bool:
    value = model_value(instance, field_name)
    if isinstance(value, bool):
        return value
    return bool(value)


def model_int(instance: object, field_name: str) -> int:
    value = model_value(instance, field_name)
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float | str):
        return int(value)
    raise TypeError(f"{field_name} must be numeric.")


def model_optional_int(instance: object, field_name: str) -> int | None:
    value = model_value(instance, field_name)
    if value is None:
        return None
    return model_int(instance, field_name)


def model_datetime(instance: object, field_name: str) -> datetime:
    value = model_value(instance, field_name)
    if isinstance(value, datetime):
        return value
    raise TypeError(f"{field_name} must be a datetime.")
