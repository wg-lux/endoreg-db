"""Read model of the clinical workflow for lx-annotate.

Wire shape: lx-annotate ``docs/guides/media-workflow-frontend-contract.md``
(media_workflow_state_machine/persistence_and_api). Every decision comes from
the reducer and the evidence guard; this module only describes the result.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum

from endoreg_db.models.media.pdf.raw_pdf import RawPdfFile
from endoreg_db.models.media.video.video_file import VideoFile
from endoreg_db.models.state.clinical_workflow import ClinicalWorkflowRecord

from .facts import report_record_facts, video_record_facts
from .persistence import stored_state
from .state_machine import (
    AnnotationStage,
    ClinicalLifecycleEvent,
    ClinicalLifecycleInvariantError,
    ClinicalLifecycleTransitionError,
    ClinicalRecordFacts,
    ClinicalRecordKind,
    ClinicalRecordState,
    PrivacyStage,
    ReleaseStage,
    derive_clinical_record_state,
    evidence_rejection,
    transition_clinical_record,
)

logger = logging.getLogger(__name__)


class WorklistStage(StrEnum):
    VALIDATE = "validate"
    ANNOTATE = "annotate"
    APPROVE_EXPORT = "approve_export"


@dataclass(frozen=True, slots=True)
class StageCount:
    stage: WorklistStage
    count: int
    transient: int


@dataclass(frozen=True, slots=True)
class StageCounts:
    stages: tuple[StageCount, ...]
    unmappable: int


def allowed_events(
    state: ClinicalRecordState, facts: ClinicalRecordFacts
) -> tuple[ClinicalLifecycleEvent, ...]:
    """Events the reducer and the evidence guard accept in ``state``.

    This is not a list of events a person may trigger.
    """
    permitted: list[ClinicalLifecycleEvent] = []
    for event in ClinicalLifecycleEvent:
        if evidence_rejection(facts, event) is not None:
            continue
        try:
            transition_clinical_record(state, event)
        except ClinicalLifecycleTransitionError:
            continue
        permitted.append(event)
    return tuple(permitted)


_INACTIVE_PRIVACY = frozenset(
    {PrivacyStage.FAILED, PrivacyStage.LOST, PrivacyStage.WITHDRAWN}
)
_IMPORT_FEED = frozenset({PrivacyStage.IMPORT_PENDING, PrivacyStage.IMPORTING})


def _accumulate(
    totals: dict[WorklistStage, list[int]], state: ClinicalRecordState
) -> None:
    # count = waits on a person; transient = a system step feeds the stage.
    privacy, annotation, release = state.privacy, state.annotation, state.release
    if privacy is PrivacyStage.AWAITING_VALIDATION:
        totals[WorklistStage.VALIDATE][0] += 1
    if privacy in _IMPORT_FEED:
        totals[WorklistStage.VALIDATE][1] += 1
        if annotation is AnnotationStage.NOT_AVAILABLE:
            totals[WorklistStage.ANNOTATE][1] += 1
    if annotation is AnnotationStage.IN_PROGRESS and privacy not in _INACTIVE_PRIVACY:
        totals[WorklistStage.ANNOTATE][0] += 1
    if annotation is AnnotationStage.VALIDATED and release is ReleaseStage.NOT_RELEASED:
        if privacy is PrivacyStage.RAW_PURGED:
            totals[WorklistStage.APPROVE_EXPORT][0] += 1
        elif privacy is PrivacyStage.VALIDATED:
            totals[WorklistStage.APPROVE_EXPORT][1] += 1


def _stored_rows(
    kind: ClinicalRecordKind, record_ids: list[int]
) -> Mapping[int, ClinicalWorkflowRecord]:
    rows = ClinicalWorkflowRecord.objects.filter(
        kind=kind.value, record_id__in=record_ids
    )
    return {int(row.record_id): row for row in rows}


def stage_counts(
    videos: Iterable[VideoFile], reports: Iterable[RawPdfFile]
) -> StageCounts:
    """Worklist counts over already center-scoped records.

    Stored state wins; records without a row are projected from facts.
    Records whose facts violate lifecycle invariants are counted as
    ``unmappable`` and logged, never assigned a stage.
    """
    totals: dict[WorklistStage, list[int]] = {stage: [0, 0] for stage in WorklistStage}
    unmappable = 0
    video_list = list(videos)
    report_list = list(reports)
    groups: tuple[
        tuple[ClinicalRecordKind, list[VideoFile] | list[RawPdfFile]], ...
    ] = (
        (ClinicalRecordKind.VIDEO, video_list),
        (ClinicalRecordKind.REPORT, report_list),
    )
    for kind, records in groups:
        rows = _stored_rows(kind, [int(record.pk) for record in records])
        for record in records:
            row = rows.get(int(record.pk))
            if row is not None:
                _accumulate(totals, stored_state(row))
                continue
            facts = (
                video_record_facts(record)
                if isinstance(record, VideoFile)
                else report_record_facts(record)
            )
            try:
                state = derive_clinical_record_state(facts)
            except ClinicalLifecycleInvariantError as exc:
                unmappable += 1
                logger.warning(
                    "clinical workflow record unmappable: kind=%s id=%s violations=%s",
                    kind.value,
                    record.pk,
                    ",".join(violation.value for violation in exc.violations),
                )
                continue
            _accumulate(totals, state)
    stages = tuple(
        StageCount(stage, totals[stage][0], totals[stage][1]) for stage in WorklistStage
    )
    return StageCounts(stages=stages, unmappable=unmappable)
