from __future__ import annotations
from enum import StrEnum

from django.db import models

from endoreg_db.utils.rust_backend import (
    anonymization_status_rules,
    derive_anonymization_status as rust_derive_anonymization_status,
    derive_report_anonymization_status as rust_derive_report_anonymization_status,
)


class AnonymizationState(StrEnum):
    """Wire tokens for the authoritative Rust import-state rules."""

    NOT_STARTED = "not_started"
    EXTRACTING_FRAMES = "extracting_frames"
    PROCESSING_ANONYMIZING = "processing_anonymization"
    DONE_PROCESSING_ANONYMIZATION = "done_processing_anonymization"
    VALIDATED = "validated"
    FAILED = "failed"
    STARTED = "started"
    ANONYMIZED = "anonymized"


def derive_video_anonymization_state(
    *,
    processing_error: bool,
    anonymization_validated: bool,
    sensitive_meta_processed: bool,
    frames_extracted: bool,
    anonymized: bool,
    was_created: bool,
    processing_started: bool,
) -> AnonymizationState:
    status = rust_derive_anonymization_status(
        processing_error=processing_error,
        anonymization_validated=anonymization_validated,
        sensitive_meta_processed=sensitive_meta_processed,
        frames_extracted=frames_extracted,
        anonymized=anonymized,
        was_created=was_created,
        processing_started=processing_started,
    )
    return AnonymizationState(status)


def derive_report_anonymization_state(
    *,
    processing_error: bool,
    anonymization_validated: bool,
    sensitive_meta_processed: bool,
    anonymized: bool,
    processing_started: bool,
) -> AnonymizationState:
    status = rust_derive_report_anonymization_status(
        processing_error=processing_error,
        anonymization_validated=anonymization_validated,
        sensitive_meta_processed=sensitive_meta_processed,
        anonymized=anonymized,
        processing_started=processing_started,
    )
    return AnonymizationState(status)


def anonymization_status_case(
    *,
    report: bool = False,
    relation_prefix: str = "",
    include_missing_relation: bool = False,
) -> models.Case:
    """Translate native ordered rules into SQL without duplicating precedence."""
    prefix = f"{relation_prefix}__" if relation_prefix else ""
    whens = [
        models.When(
            **{f"{prefix}{field}": value for field, value in conditions},
            then=models.Value(AnonymizationState(status).value),
        )
        for status, conditions in anonymization_status_rules(report=report)
    ]
    if include_missing_relation:
        whens.insert(
            0,
            models.When(
                **{f"{prefix}isnull": True},
                then=models.Value(AnonymizationState.NOT_STARTED.value),
            ),
        )
    return models.Case(
        *whens,
        default=models.Value(AnonymizationState.NOT_STARTED.value),
        output_field=models.CharField(),
    )
