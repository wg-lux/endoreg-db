"""Read model of the clinical workflow for lx-annotate.

Wire shape: lx-annotate ``docs/guides/media-workflow-frontend-contract.md``
(media_workflow_state_machine/persistence_and_api). Every decision comes from
the reducer and the evidence guard; this module only describes the result.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass

from lx_dtypes.models.contracts.media_workflow import PRIVACY_INACTIVE, WorklistStage
from lx_dtypes.models.contracts.media_workflow_api import StageCount, StageCounts

from endoreg_db.models.media.pdf.raw_pdf import RawPdfFile
from endoreg_db.models.media.video.video_file import VideoFile

from .facts import report_record_facts, video_record_facts
from .persistence import (
    WorkflowStateDriftError,
    resolve_workflow_state,
    stored_workflow_rows,
)
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
    guarded_clinical_transition,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class WorklistStageCounts:
    """Contract stage rows plus the records no stage may be assigned to.

    ``unmappable`` is part of the endoreg_db wire shape of
    ``workflow/stages/`` but not of the lx-dtypes ``StageCounts`` model.
    """

    stages: StageCounts
    unmappable: int

    def __post_init__(self) -> None:
        if self.unmappable < 0:
            raise ValueError("unmappable must not be negative")


def allowed_events(
    state: ClinicalRecordState, facts: ClinicalRecordFacts
) -> tuple[ClinicalLifecycleEvent, ...]:
    """Events the reducer and the evidence guard accept in ``state``.

    This is not a list of events a person may trigger.
    """
    permitted: list[ClinicalLifecycleEvent] = []
    for event in ClinicalLifecycleEvent:
        try:
            guarded_clinical_transition(state, facts, event)
        except (ClinicalLifecycleTransitionError, ClinicalLifecycleInvariantError):
            continue
        permitted.append(event)
    return tuple(permitted)


# Worklist feed of the validate stage; the contract declares no import set.
_IMPORT_FEED = frozenset({PrivacyStage.IMPORT_PENDING, PrivacyStage.IMPORTING})


def _accumulate(
    totals: dict[WorklistStage, list[int]],
    state: ClinicalRecordState,
    kind: ClinicalRecordKind,
) -> None:
    # count = waits on a person; transient = a system step feeds the stage.
    privacy, annotation, release = state.privacy, state.annotation, state.release
    if privacy is PrivacyStage.AWAITING_VALIDATION:
        totals[WorklistStage.VALIDATE][0] += 1
    if privacy in _IMPORT_FEED:
        totals[WorklistStage.VALIDATE][1] += 1
        if annotation is AnnotationStage.NOT_AVAILABLE:
            totals[WorklistStage.ANNOTATE][1] += 1
    if annotation is AnnotationStage.IN_PROGRESS and privacy not in PRIVACY_INACTIVE:
        totals[WorklistStage.ANNOTATE][0] += 1
    if (
        kind is ClinicalRecordKind.VIDEO
        and annotation is AnnotationStage.VALIDATED
        and release is ReleaseStage.NOT_RELEASED
    ):
        if privacy is PrivacyStage.RAW_PURGED:
            totals[WorklistStage.APPROVE_EXPORT][0] += 1
        elif privacy is PrivacyStage.VALIDATED:
            totals[WorklistStage.APPROVE_EXPORT][1] += 1


def stage_counts(
    videos: Iterable[VideoFile], reports: Iterable[RawPdfFile]
) -> WorklistStageCounts:
    """Worklist counts over already center-scoped records.

    Each record resolves through ``resolve_workflow_state``. Records whose
    facts violate lifecycle invariants, or whose stored row disagrees with
    them, are counted as ``unmappable`` and logged, never assigned a stage.
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
        rows = stored_workflow_rows(kind, [int(record.pk) for record in records])
        for record in records:
            facts = (
                video_record_facts(record)
                if isinstance(record, VideoFile)
                else report_record_facts(record)
            )
            try:
                snapshot = resolve_workflow_state(
                    kind, int(record.pk), facts, rows.get(int(record.pk))
                )
            except WorkflowStateDriftError:
                # resolve_workflow_state already emitted the structured event.
                unmappable += 1
                continue
            except ClinicalLifecycleInvariantError as exc:
                unmappable += 1
                logger.warning(
                    "clinical workflow record unmappable: kind=%s id=%s violations=%s",
                    kind.value,
                    record.pk,
                    ",".join(violation.value for violation in exc.violations),
                )
                continue
            _accumulate(totals, snapshot.state, kind)
    stages = StageCounts(
        stages=tuple(
            StageCount(stage=stage, count=totals[stage][0], transient=totals[stage][1])
            for stage in WorklistStage
        )
    )
    return WorklistStageCounts(stages=stages, unmappable=unmappable)
