from __future__ import annotations

DEFAULT_PROCESSOR_FALLBACK_NAME = "olympus_cv_1500"
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


def get_default_video_processor_name() -> str | None:
    from endoreg_db.models.medical.hardware import EndoscopyProcessor
    from endoreg_db.utils.set_default_center import get_default_processor

    configured_processor = get_default_processor()
    configured_name = getattr(configured_processor, "name", None)
    if configured_name:
        return str(configured_name)

    named_fallback = (
        EndoscopyProcessor.objects.filter(name=DEFAULT_PROCESSOR_FALLBACK_NAME)
        .order_by("pk")
        .first()
    )
    if named_fallback is not None and named_fallback.name:
        return str(named_fallback.name)

    fallback = EndoscopyProcessor.objects.order_by("pk").first()
    fallback_name = getattr(fallback, "name", None)
    return str(fallback_name) if fallback_name else None


def resolve_processor_name_for_import(processor_name: str | None) -> str | None:
    if not _is_undefined_processor_name(processor_name):
        return _normalize_processor_name(processor_name)
    return get_default_video_processor_name()
