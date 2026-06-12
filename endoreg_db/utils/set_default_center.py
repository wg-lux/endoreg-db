from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, cast

from django.core.exceptions import ObjectDoesNotExist

from endoreg_db.models import AIDataSet, ApplicationSettings, Center, EndoscopyProcessor

_UNSET = object()


class _ApplicationSettingsLike(Protocol):
    center: Center | None
    processor: EndoscopyProcessor | None
    ai_dataset: AIDataSet | None
    annotator_name: str
    report_template_name: str
    ai_dataset_id: int | None
    ai_dataset_name: str
    ai_dataset_type: str

    def save(self, *, update_fields: list[str] | None = None) -> None: ...


@dataclass(frozen=True)
class application_defaults_snapshot:
    center_id: int | None
    center_name: str | None
    processor_id: int | None
    processor_name: str | None
    annotator_name: str
    report_template_name: str
    ai_dataset_id: int | None
    ai_dataset_name: str
    ai_dataset_type: str


def get_application_settings() -> ApplicationSettings:
    return ApplicationSettings.get_solo()


def get_application_defaults() -> application_defaults_snapshot:
    settings_obj = cast(_ApplicationSettingsLike, get_application_settings())
    center = settings_obj.center
    processor = settings_obj.processor
    return application_defaults_snapshot(
        center_id=getattr(center, "pk", None),
        center_name=getattr(center, "name", None),
        processor_id=getattr(processor, "pk", None),
        processor_name=getattr(processor, "name", None),
        annotator_name=settings_obj.annotator_name or "",
        report_template_name=settings_obj.report_template_name or "",
        ai_dataset_id=settings_obj.ai_dataset_id,
        ai_dataset_name=settings_obj.ai_dataset_name or "",
        ai_dataset_type=settings_obj.ai_dataset_type or "",
    )


def _resolve_center(center: int | str | Center | None) -> Center | None:
    if center is None:
        return None
    if isinstance(center, Center):
        return center
    if isinstance(center, int):
        return Center.objects.filter(pk=center).first()
    return Center.objects.filter(name=center).first()


def _resolve_processor(
    processor: int | str | EndoscopyProcessor | None,
) -> EndoscopyProcessor | None:
    if processor is None:
        return None
    if isinstance(processor, EndoscopyProcessor):
        return processor
    if isinstance(processor, int):
        return EndoscopyProcessor.objects.filter(pk=processor).first()
    return EndoscopyProcessor.objects.filter(name=processor).first()


def _resolve_ai_dataset(ai_dataset: int | AIDataSet | None) -> AIDataSet | None:
    if ai_dataset is None:
        return None
    if isinstance(ai_dataset, AIDataSet):
        return ai_dataset
    return AIDataSet.objects.filter(pk=ai_dataset).first()


def set_default_center(center: int | str | Center | None) -> ApplicationSettings:
    settings_obj = cast(_ApplicationSettingsLike, get_application_settings())
    settings_obj.center = _resolve_center(center)
    settings_obj.save(update_fields=["center", "updated_at"])
    return cast(ApplicationSettings, settings_obj)


def update_application_defaults(
    *,
    center: int | str | Center | None = None,
    processor: int | str | EndoscopyProcessor | None = None,
    annotator_name: str | None = None,
    report_template_name: str | None = None,
    ai_dataset: int | AIDataSet | None | object = _UNSET,
    ai_dataset_name: str | None = None,
    ai_dataset_type: str | None = None,
) -> ApplicationSettings:
    settings_obj = cast(_ApplicationSettingsLike, get_application_settings())

    if center is not None:
        settings_obj.center = _resolve_center(center)
    if processor is not None:
        settings_obj.processor = _resolve_processor(processor)
    if annotator_name is not None:
        settings_obj.annotator_name = annotator_name
    if report_template_name is not None:
        settings_obj.report_template_name = report_template_name
    if ai_dataset is not _UNSET:
        settings_obj.ai_dataset = _resolve_ai_dataset(
            cast(int | AIDataSet | None, ai_dataset)
        )
    if ai_dataset_name is not None:
        settings_obj.ai_dataset_name = ai_dataset_name
    if ai_dataset_type is not None:
        settings_obj.ai_dataset_type = ai_dataset_type

    settings_obj.save()
    return cast(ApplicationSettings, settings_obj)


def require_default_center() -> Center:
    settings_obj = cast(_ApplicationSettingsLike, get_application_settings())
    if settings_obj.center is None:
        raise ObjectDoesNotExist(
            "ApplicationSettings.center is not configured. Set it in the Application Settings admin."
        )
    return settings_obj.center


def get_default_processor() -> EndoscopyProcessor | None:
    return cast(_ApplicationSettingsLike, get_application_settings()).processor


def get_default_annotator_name(default: str = "") -> str:
    value = cast(_ApplicationSettingsLike, get_application_settings()).annotator_name
    return value or default


def get_default_report_template_name(default: str = "") -> str:
    value = cast(
        _ApplicationSettingsLike, get_application_settings()
    ).report_template_name
    return value or default


def get_default_ai_dataset_name(default: str = "") -> str:
    value = cast(_ApplicationSettingsLike, get_application_settings()).ai_dataset_name
    return value or default


def get_default_ai_dataset_id() -> int | None:
    return cast(_ApplicationSettingsLike, get_application_settings()).ai_dataset_id


def get_default_ai_dataset_type(default: str = "") -> str:
    value = cast(_ApplicationSettingsLike, get_application_settings()).ai_dataset_type
    return value or default


__all__ = [
    "application_defaults_snapshot",
    "get_application_defaults",
    "get_application_settings",
    "get_default_annotator_name",
    "get_default_ai_dataset_id",
    "get_default_ai_dataset_name",
    "get_default_ai_dataset_type",
    "get_default_processor",
    "get_default_report_template_name",
    "require_default_center",
    "set_default_center",
    "update_application_defaults",
]
