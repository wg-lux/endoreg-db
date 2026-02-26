from __future__ import annotations

from dataclasses import dataclass

from django.core.exceptions import ObjectDoesNotExist

from endoreg_db.models import ApplicationSettings, Center, EndoscopyProcessor


@dataclass(frozen=True)
class application_defaults_snapshot:
    center_id: int | None
    center_name: str | None
    processor_id: int | None
    processor_name: str | None
    annotator_name: str
    report_template_name: str


def get_application_settings() -> ApplicationSettings:
    return ApplicationSettings.get_solo()


def get_application_defaults() -> application_defaults_snapshot:
    settings_obj = get_application_settings()
    center = settings_obj.center
    processor = settings_obj.processor
    return application_defaults_snapshot(
        center_id=getattr(center, "pk", None),
        center_name=getattr(center, "name", None),
        processor_id=getattr(processor, "pk", None),
        processor_name=getattr(processor, "name", None),
        annotator_name=settings_obj.annotator_name or "",
        report_template_name=settings_obj.report_template_name or "",
    )


def _resolve_center(center: int | str | Center | None) -> Center | None:
    if center is None:
        return None
    if isinstance(center, Center):
        return center
    if isinstance(center, int):
        return Center.objects.filter(pk=center).first()
    if isinstance(center, str):
        return Center.objects.filter(name=center).first()
    raise TypeError(f"Unsupported center value: {type(center)!r}")


def _resolve_processor(
    processor: int | str | EndoscopyProcessor | None,
) -> EndoscopyProcessor | None:
    if processor is None:
        return None
    if isinstance(processor, EndoscopyProcessor):
        return processor
    if isinstance(processor, int):
        return EndoscopyProcessor.objects.filter(pk=processor).first()
    if isinstance(processor, str):
        return EndoscopyProcessor.objects.filter(name=processor).first()
    raise TypeError(f"Unsupported processor value: {type(processor)!r}")


def set_default_center(center: int | str | Center | None) -> ApplicationSettings:
    settings_obj = get_application_settings()
    settings_obj.center = _resolve_center(center)
    settings_obj.save(update_fields=["center", "updated_at"])
    return settings_obj


def update_application_defaults(
    *,
    center: int | str | Center | None = None,
    processor: int | str | EndoscopyProcessor | None = None,
    annotator_name: str | None = None,
    report_template_name: str | None = None,
) -> ApplicationSettings:
    settings_obj = get_application_settings()

    if center is not None:
        settings_obj.center = _resolve_center(center)
    if processor is not None:
        settings_obj.processor = _resolve_processor(processor)
    if annotator_name is not None:
        settings_obj.annotator_name = annotator_name
    if report_template_name is not None:
        settings_obj.report_template_name = report_template_name

    settings_obj.save()
    return settings_obj


def require_default_center() -> Center:
    settings_obj = get_application_settings()
    if settings_obj.center is None:
        raise ObjectDoesNotExist(
            "ApplicationSettings.center is not configured. Set it in the Application Settings admin."
        )
    return settings_obj.center


def get_default_processor() -> EndoscopyProcessor | None:
    return get_application_settings().processor


def get_default_annotator_name(default: str = "") -> str:
    value = get_application_settings().annotator_name
    return value or default


def get_default_report_template_name(default: str = "") -> str:
    value = get_application_settings().report_template_name
    return value or default


__all__ = [
    "application_defaults_snapshot",
    "get_application_defaults",
    "get_application_settings",
    "get_default_annotator_name",
    "get_default_processor",
    "get_default_report_template_name",
    "require_default_center",
    "set_default_center",
    "update_application_defaults",
]
