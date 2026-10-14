"""Typed results shared by annotation comparison services and the HTTP boundary."""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class FindingComparison:
    practitioner: str
    kind: str
    report: int | None
    matching: list[str]
    own: list[str]
    other: list[str]
    overlap: float | None


@dataclass(frozen=True)
class AnnotationComparison:
    practitioner: str
    shared: int
    agreeing: int
    agreement: float | None
    intersection: float | None
    union: float | None
    overlap: float | None
    missing: int


@dataclass
class VideoComparison:
    video: int
    annotations: list[AnnotationComparison] = field(
        default_factory=list[AnnotationComparison]
    )
    findings: list[FindingComparison] = field(default_factory=list[FindingComparison])
    conflicts: int = 0
    unmapped: list[str] = field(default_factory=list[str])
    unavailable: list[str] = field(default_factory=list[str])
