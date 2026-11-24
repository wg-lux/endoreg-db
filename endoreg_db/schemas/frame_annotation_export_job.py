"""Persisted payloads of browser-started frame annotation export jobs.

The job configuration is the same lx_dtypes contract that validates YAML export
configs, so form, YAML file and ``scripts/export_frame_annotations.py`` share
one shape.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import cast

from lx_dtypes.models.contracts.video_frame_export import (
    VideoFrameAnnotationExportConfigPayload,
    YamlValue,
    validate_video_frame_annotation_export_config,
)
from pydantic import BaseModel, ConfigDict, Field, ValidationError


class FrameAnnotationExportJobResult(BaseModel):
    """Counts and locations relative to the protected annotation export root."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    output_path: str = Field(min_length=1)
    row_count: int = Field(ge=0)
    exported_video_count: int = Field(ge=0)
    exported_frame_count: int = Field(ge=0)
    video_output_dir: str | None = None
    frame_output_dir: str | None = None


def without_null_values(payload: Mapping[str, YamlValue]) -> dict[str, YamlValue]:
    """Omit null only for declared optional fields whose default is already null.

    Every nullable field of ``VideoFrameAnnotationExportConfigPayload`` defaults
    to null, so this is lossless; ``limit: null`` would otherwise raise a
    ``TypeError`` from its ``ge`` constraint instead of a validation error.
    Fixed in lx-data-models (``limit: NonNegativeInt | JsonNull``); remove this
    helper once the pinned lx-dtypes release contains that fix.
    """
    fields = VideoFrameAnnotationExportConfigPayload.model_fields
    return {
        key: value
        for key, value in payload.items()
        if value is not None or key not in fields or fields[key].default is not None
    }


def validate_frame_annotation_export_job_config(
    value: object,
) -> VideoFrameAnnotationExportConfigPayload:
    """Reject malformed external values, including TypeError from shared constraints."""
    payload = (
        without_null_values(cast(dict[str, YamlValue], value))
        if isinstance(value, dict)
        else cast(YamlValue, value)
    )
    try:
        return validate_video_frame_annotation_export_config(payload)
    except TypeError as exc:
        raise ValidationError.from_exception_data(
            VideoFrameAnnotationExportConfigPayload.__name__,
            [
                {
                    "type": "value_error",
                    "loc": (),
                    "input": payload,
                    "ctx": {
                        "error": ValueError("Invalid export configuration value type.")
                    },
                }
            ],
        ) from exc


def dump_frame_annotation_export_job_config(value: object) -> dict[str, YamlValue]:
    config = (
        value
        if isinstance(value, VideoFrameAnnotationExportConfigPayload)
        else validate_frame_annotation_export_job_config(value)
    )
    return cast(dict[str, YamlValue], config.model_dump(mode="json", exclude_none=True))


def dump_frame_annotation_export_job_result(
    value: object,
) -> dict[str, YamlValue] | None:
    if value is None:
        return None
    result = (
        value
        if isinstance(value, FrameAnnotationExportJobResult)
        else FrameAnnotationExportJobResult.model_validate(value)
    )
    return cast(dict[str, YamlValue], result.model_dump(mode="json"))


__all__ = [
    "FrameAnnotationExportJobResult",
    "dump_frame_annotation_export_job_config",
    "dump_frame_annotation_export_job_result",
    "validate_frame_annotation_export_job_config",
    "without_null_values",
]
