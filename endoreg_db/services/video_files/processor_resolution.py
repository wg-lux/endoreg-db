from __future__ import annotations

_UNDEFINED_PROCESSOR_NAMES = {"unknown", "undefined", "none", "null"}


def _normalize_processor_name(processor_name: str | None) -> str | None:
    if processor_name is None:
        return None
    normalized = str(processor_name).strip()
    return normalized or None


def _is_undefined_processor_name(processor_name: str | None) -> bool:
    normalized = _normalize_processor_name(processor_name)
    if normalized is None:
        return True
    return normalized.casefold() in _UNDEFINED_PROCESSOR_NAMES


def get_default_video_processor_name() -> str:
    from endoreg_db.utils.set_default_center import get_default_processor

    configured_processor = get_default_processor()
    configured_name = _normalize_processor_name(
        configured_processor.name if configured_processor is not None else None
    )
    if configured_name is None or _is_undefined_processor_name(configured_name):
        raise ValueError(
            "Video import requires an explicit processor name or a defined "
            "ApplicationSettings.processor configuration."
        )
    return configured_name


def resolve_processor_name_for_import(processor_name: str | None) -> str:
    normalized = _normalize_processor_name(processor_name)
    if normalized is not None and not _is_undefined_processor_name(normalized):
        return normalized
    return get_default_video_processor_name()
