"""Read-only comparisons of existing practitioner tracks, never shared edits."""

from collections import defaultdict
from collections.abc import Mapping
from math import isfinite
from typing import cast

from django.db.models import QuerySet
from lx_dtypes.models.contracts.dtypes_record_persistence import (
    parse_dtypes_record_persistence_payload,
)
from lx_dtypes.models.contracts.finding_review import AnnotationFindingMappings
from lx_dtypes.terminology.terminology_loader import resolve_module_path
from lx_dtypes.utils.study_setup_yaml import parse_setup_yaml

from endoreg_db.models.label.annotation.image_classification import (
    ImageClassificationAnnotation,
)
from endoreg_db.models.media.video.video_file import VideoFile
from endoreg_db.models.report.patient_examination_report import PatientExaminationReport
from endoreg_db.schemas.annotation_comparisons import (
    AnnotationComparison,
    FindingComparison,
    VideoComparison,
)
from endoreg_db.schemas.persisted_json import VideoFileMetaPayload
from endoreg_db.services.annotations.segment_identity import (
    PREDICTION_INFORMATION_SOURCE_NAMES,
    is_segment_derived_external_annotation_id,
    manual_annotation_filter,
)


def human_annotations() -> QuerySet[ImageClassificationAnnotation]:
    return (
        ImageClassificationAnnotation.objects.filter(
            manual_annotation_filter(),
            annotator__isnull=False,
        )
        .exclude(annotator="")
        .exclude(information_source__name__in=PREDICTION_INFORMATION_SOURCE_NAMES)
        .exclude(information_source__information_source_types__name="prediction")
        .distinct()
    )


def compare_annotations(
    practitioner: str,
    own: Mapping[tuple[int, str], bool],
    other: Mapping[tuple[int, str], bool],
    durations: Mapping[int, float],
) -> AnnotationComparison:
    shared = own.keys() & other.keys()
    agreeing = sum(own[key] == other[key] for key in shared)
    positive_own = {key for key, value in own.items() if value}
    positive_other = {key for key, value in other.items() if value}
    union = positive_own | positive_other
    intersection = positive_own & positive_other
    missing = sum(frame not in durations for frame, _ in union)
    if any(not isfinite(duration) or duration <= 0 for duration in durations.values()):
        raise ValueError("Frame durations must be finite and positive")
    seconds = None if missing else sum(durations[frame] for frame, _ in union)
    common = None if missing else sum(durations[frame] for frame, _ in intersection)
    return AnnotationComparison(
        practitioner=practitioner,
        shared=len(shared),
        agreeing=agreeing,
        agreement=100 * agreeing / len(shared) if shared else None,
        intersection=common,
        union=seconds,
        overlap=100 * common / seconds if seconds and common is not None else None,
        missing=missing,
    )


def compare_findings(
    practitioner: str,
    source: str,
    own: set[str],
    other: set[str],
    report: int | None = None,
) -> FindingComparison:
    union = own | other
    return FindingComparison(
        practitioner,
        source,
        report,
        sorted(own & other),
        sorted(own - other),
        sorted(other - own),
        100 * len(own & other) / len(union) if union else None,
    )


def _durations(video: VideoFile) -> dict[int, float]:
    # Never infer a variable frame duration from nominal frames per second.
    timestamps = {
        int(number): float(timestamp)
        for number, timestamp in video.frames.filter(
            timestamp__isnull=False
        ).values_list("frame_number", "timestamp")
    }
    payload = VideoFileMetaPayload.model_validate(video.meta or {})
    timeline = payload.source_timeline
    if timeline is not None:
        if (
            timeline.artifact_kind == "processed"
            and timeline.content_hash != video.processed_video_hash
        ):
            return {}
        source = timeline.source.timeline
        timestamps[source.frame_count] = (
            timeline.first_timestamp_seconds + source.duration_seconds
        )
    return {
        number: timestamps[number + 1] - timestamp
        for number, timestamp in timestamps.items()
        if number + 1 in timestamps
        and isfinite(timestamp)
        and isfinite(timestamps[number + 1])
        and timestamps[number + 1] > timestamp
    }


def _mapping(module: str, version: str) -> AnnotationFindingMappings | None:
    path = resolve_module_path(module, version=version) / "annotation_mappings.yml"
    if not path.exists():
        return None
    mapping = AnnotationFindingMappings.model_validate(
        parse_setup_yaml(path.read_text())
    )
    if (mapping.knowledge_base_module, mapping.knowledge_base_version) != (
        module,
        version,
    ):
        raise ValueError("Annotation mapping terminology identity mismatch")
    return mapping


def video_comparisons(
    video: VideoFile,
    *,
    principal: str,
    reports: QuerySet[PatientExaminationReport],
) -> VideoComparison:
    result = VideoComparison(video=int(video.pk))
    # Collapsing identical observations avoids weighting duplicate sources twice.
    observations: dict[str, dict[tuple[int, str], set[bool]]] = defaultdict(
        lambda: defaultdict(set)
    )
    modalities: dict[str, set[tuple[str, str]]] = defaultdict(set)
    for author, number, label, value, external in (
        human_annotations()
        .filter(frame__video=video)
        .values_list(
            "annotator",
            "frame__frame_number",
            "label__name",
            "value",
            "external_annotation_id",
        )
    ):
        if not str(author).strip():
            continue
        observations[str(author)][(int(number), str(label))].add(bool(value))
        if value:
            modalities[str(author)].add(
                (
                    str(label),
                    "segment"
                    if is_segment_derived_external_annotation_id(external)
                    else "frame",
                )
            )
    tracks = {
        author: {
            key: next(iter(values)) for key, values in track.items() if len(values) == 1
        }
        for author, track in observations.items()
    }
    result.conflicts = sum(
        len(values) > 1 for track in observations.values() for values in track.values()
    )
    own = tracks.get(principal, {})
    durations = _durations(video)
    for author in sorted(tracks.keys() - {principal}):
        ambiguous = {
            key
            for person in (principal, author)
            for key, values in observations[person].items()
            if len(values) > 1
        }
        result.annotations.append(
            compare_annotations(
                author,
                {key: value for key, value in own.items() if key not in ambiguous},
                {
                    key: value
                    for key, value in tracks[author].items()
                    if key not in ambiguous
                },
                durations,
            )
        )

    examination = video.examination
    if examination is None and video.sensitive_meta is not None:
        examination = video.sensitive_meta.pseudo_examination
    if examination is None:
        result.unavailable.append("missing_examination")
        return result
    module, version = (
        examination.knowledge_base_module,
        examination.knowledge_base_version,
    )
    if not module or not version:
        result.unavailable.append("missing_terminology")
        return result
    mapping = _mapping(str(module), str(version))
    if mapping is None:
        result.unavailable.append("missing_mapping")
        return result
    finding_sets: dict[str, set[str]] = {}
    for author, track in tracks.items():
        positive_labels = {label for (_, label), value in track.items() if value}
        applicable = [
            rule
            for rule in mapping.mappings
            if rule.label in positive_labels
            and any(
                (rule.label, modality) in modalities[author]
                for modality in rule.modalities
            )
        ]
        finding_sets[author] = {rule.finding_name for rule in applicable}
        if author == principal:
            result.unmapped = sorted(
                positive_labels - {rule.label for rule in applicable}
            )
    own_findings = finding_sets.get(principal, set())
    if not own_findings:
        result.unavailable.append("no_mapped_positive_findings")
        return result
    for author in sorted(finding_sets.keys() - {principal}):
        if not finding_sets[author]:
            result.unavailable.append("peer_without_mapped_positive_findings")
            continue
        result.findings.append(
            compare_findings(author, "annotations", own_findings, finding_sets[author])
        )
    for report in (
        reports.filter(
            patient_examination=examination,
            status=PatientExaminationReport.Status.FINAL,
            is_active=True,
        )
        .select_related("finalized_by")
        .order_by("pk")
    ):
        if report.finalized_by is None or report.dtypes_record is None:
            result.unavailable.append("report_missing_attribution_or_snapshot")
            continue
        author = str(report.finalized_by.username)
        if author == principal:
            continue
        snapshot = parse_dtypes_record_persistence_payload(report.dtypes_record)
        if (snapshot.knowledge_base_module, snapshot.knowledge_base_version) != (
            module,
            version,
        ):
            result.unavailable.append("report_terminology_mismatch")
            continue
        result.findings.append(
            compare_findings(
                author,
                "report",
                own_findings,
                {item.finding for item in snapshot.patient_findings},
                cast(int, report.pk),
            )
        )
    result.unavailable = sorted(set(result.unavailable))
    return result
