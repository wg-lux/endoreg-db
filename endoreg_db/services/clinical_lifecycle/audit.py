"""Read-only conformance audit of persisted records against the lifecycle.

The report contains record kinds, primary keys, stages and stable violation
codes only. It never contains patient identifiers or file paths.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field

from endoreg_db.models.media.pdf.raw_pdf import RawPdfFile
from endoreg_db.models.media.video.video_file import VideoFile

from .facts import report_record_facts, video_record_facts
from .state_machine import (
    ClinicalLifecycleInvariantError,
    ClinicalRecordFacts,
    ClinicalRecordKind,
    derive_clinical_record_state,
)


@dataclass(frozen=True, slots=True)
class LifecycleViolationRow:
    kind: ClinicalRecordKind
    record_id: int
    violations: tuple[str, ...]


@dataclass(slots=True)
class ClinicalLifecycleAudit:
    stage_counts: Counter[str] = field(default_factory=lambda: Counter[str]())
    legacy_identity_exemptions: int = 0
    violations: list[LifecycleViolationRow] = field(
        default_factory=lambda: list[LifecycleViolationRow]()
    )

    def to_json(self) -> dict[str, object]:
        return {
            "stage_counts": dict(sorted(self.stage_counts.items())),
            "legacy_identity_exemptions": self.legacy_identity_exemptions,
            "violation_count": len(self.violations),
            "violations": [
                {
                    "kind": row.kind.value,
                    "record_id": row.record_id,
                    "violations": list(row.violations),
                }
                for row in self.violations
            ],
        }


def _record(audit: ClinicalLifecycleAudit, facts: ClinicalRecordFacts) -> None:
    if facts.legacy_identity_exemption:
        audit.legacy_identity_exemptions += 1
    try:
        state = derive_clinical_record_state(facts)
    except ClinicalLifecycleInvariantError as exc:
        audit.violations.append(
            LifecycleViolationRow(
                kind=exc.kind,
                record_id=exc.record_id,
                violations=tuple(violation.value for violation in exc.violations),
            )
        )
        return
    audit.stage_counts[
        f"{facts.kind.value}:{state.privacy.value}:"
        f"{state.annotation.value}:{state.release.value}"
    ] += 1


def audit_clinical_records(
    kinds: Iterable[ClinicalRecordKind],
) -> ClinicalLifecycleAudit:
    audit = ClinicalLifecycleAudit()
    for kind in kinds:
        match kind:
            case ClinicalRecordKind.VIDEO:
                videos = VideoFile.objects.select_related(
                    "state", "sensitive_meta", "sensitive_meta__state"
                ).order_by("pk")
                for video in videos.iterator(chunk_size=500):
                    _record(audit, video_record_facts(video))
            case ClinicalRecordKind.REPORT:
                reports = RawPdfFile.objects.select_related(
                    "state", "sensitive_meta", "sensitive_meta__state"
                ).order_by("pk")
                for report in reports.iterator(chunk_size=500):
                    _record(audit, report_record_facts(report))
    return audit


__all__ = [
    "ClinicalLifecycleAudit",
    "LifecycleViolationRow",
    "audit_clinical_records",
]
