from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
import platform
import re
import secrets
import subprocess
import sys
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import django
from django.conf import settings
from django.db import connection
from django.db.models import Q
from lx_dtypes.models.contracts import (
    LxAnonymizerPerformancePayload,
    LxAnonymizerPerformanceRunPayload,
)
from lx_dtypes.models.contracts.anonymization_quality import (
    AnonymizationQualityPayload,
    AnonymizationQualityResult,
)

from endoreg_db.config.env import BASE_DIR
from endoreg_db.models.label.annotation.frame_box import FrameBoxAnnotation
from endoreg_db.models.media.anonymization_metrics import AnonymizationFieldMetric
from endoreg_db.models.media.pdf.raw_pdf import RawPdfFile
from endoreg_db.models.media.video.video_file import VideoFile
from endoreg_db.services.anonymization_metrics import (
    PHI_REGION_ANNOTATOR,
    PHI_REGION_INFORMATION_SOURCE_NAME,
    PHI_REGION_LABEL_NAME,
)
from endoreg_db.services.anonymization_quality_evaluation import (
    evaluate_phi_region_confusion_matrix,
)
from endoreg_db.utils.file_operations import atomic_write_file, sha256_file

ManifestJson = dict[str, object]

DEFAULT_MANIFEST_OUTPUT_DIR = Path("/data/results/manifests")
MANIFEST_SCHEMA_VERSION = "1.0"
MANIFEST_SANITIZER_VERSION = "1.0"
_UNSAFE_MEDIA_FILENAME_RE = re.compile(
    r"(?i)\.(avi|m4v|mkv|mov|mp4|mpg|mpeg|pdf|txt|csv|json)\b"
)
_UNSAFE_PATH_RE = re.compile(r"(^|[\s=:])(/|[A-Za-z]:\\|\\\\)")
_SAFE_PUBLIC_LABEL_RE = re.compile(r"^[a-zA-Z0-9_.:-]{1,128}$")


@dataclass(frozen=True)
class _EvaluationRunContext:
    evaluation_run_id: str
    private_hash_salt: str


@dataclass
class _FieldCounter:
    support: int = 0
    exact_match_count: int = 0
    changed_count: int = 0
    missing_after_validation_count: int = 0


def write_performance_evaluation_manifest(
    payload: LxAnonymizerPerformancePayload,
    *,
    processor_name: str,
    center_name: str,
    output_dir: Path | None = None,
) -> Path:
    context = _new_evaluation_run_context()
    manifest = build_performance_evaluation_manifest(
        payload,
        processor_name=processor_name,
        center_name=center_name,
        context=context,
    )
    return _write_manifest(manifest, output_dir=output_dir)


def write_quality_evaluation_manifest(
    payload: AnonymizationQualityPayload,
    *,
    output_dir: Path | None = None,
) -> Path:
    context = _new_evaluation_run_context()
    manifest = build_quality_evaluation_manifest(payload, context=context)
    return _write_manifest(manifest, output_dir=output_dir)


def build_performance_evaluation_manifest(
    payload: LxAnonymizerPerformancePayload,
    *,
    processor_name: str,
    center_name: str,
    context: _EvaluationRunContext | None = None,
) -> ManifestJson:
    manifest_context = context or _new_evaluation_run_context()
    runs = list(payload.runs)
    asset_catalog = [
        _performance_asset_entry(run, context=manifest_context) for run in runs
    ]
    video_ids = _video_ids_from_performance_runs(runs)
    video_descriptors = _video_descriptor_payload(
        video_ids=video_ids,
        context=manifest_context,
    )
    manifest: ManifestJson = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "evaluation_run_id": manifest_context.evaluation_run_id,
        "generated_at": _now_iso(),
        "lane": "performance",
        "privacy": _privacy_payload(),
        "cohort_definition": {
            "asset_catalog": asset_catalog,
            "inclusion_exclusion": {
                "records_parsed": len(runs),
                "included_records": sum(1 for run in runs if run.ok),
                "video_records": sum(1 for run in runs if run.media_type == "video"),
                "report_records": sum(1 for run in runs if run.media_type == "report"),
                "drop_reasons": {
                    "failed_runs": payload.summary.failed_runs,
                    "short_circuited_runs": payload.summary.short_circuited_runs,
                    "pre_run_exclusions": "not_recorded_by_first_pass_manifest",
                },
                "cohort_verification_status": _cohort_status(
                    failed_runs=payload.summary.failed_runs
                ),
            },
            "video_descriptors": video_descriptors,
            "declared_processor_profile": _safe_public_label(
                processor_name,
                namespace="processor_profile",
                context=manifest_context,
            ),
            "declared_center_ref": _hash_identifier(
                manifest_context,
                namespace="center_name",
                value=center_name,
            ),
        },
        "environment": _environment_payload(),
        "resource_telemetry": _performance_resource_payload(
            runs,
            context=manifest_context,
        ),
        "quality_verification": {
            "quality_data_source": "not_included_in_performance_lane",
            "live_post_processing_ocr_pass": False,
        },
        "simulated_lane": _simulated_lane_payload(),
        "performance_summary": payload.summary.model_dump(mode="json"),
    }
    _assert_manifest_is_phi_safe(manifest)
    return manifest


def build_quality_evaluation_manifest(
    payload: AnonymizationQualityPayload,
    *,
    context: _EvaluationRunContext | None = None,
) -> ManifestJson:
    manifest_context = context or _new_evaluation_run_context()
    results = list(payload.results)
    video_ids = _video_ids_from_quality_results(results)
    pdf_ids = _pdf_ids_from_quality_results(results)
    manifest: ManifestJson = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "evaluation_run_id": manifest_context.evaluation_run_id,
        "generated_at": _now_iso(),
        "lane": "quality",
        "privacy": _privacy_payload(),
        "cohort_definition": {
            "asset_catalog": _quality_asset_catalog(
                results,
                context=manifest_context,
            ),
            "inclusion_exclusion": {
                "records_parsed": len(results),
                "included_records": len(results),
                "video_records": len(video_ids),
                "pdf_records": len(pdf_ids),
                "drop_reasons": payload.summary.status_counts,
                "cohort_verification_status": _quality_cohort_status(payload),
            },
            "video_descriptors": _video_descriptor_payload(
                video_ids=video_ids,
                context=manifest_context,
            ),
        },
        "environment": _environment_payload(),
        "resource_telemetry": {
            "strategy": "quality_lane_database_aggregation_only",
            "process_tree_tracking": "not_measured",
            "io_metrics": "not_measured",
            "peak_allocation_monitoring": "not_measured",
        },
        "quality_verification": {
            "quality_data_source": (
                "stored_residual_text_fields_and_frame_box_annotations"
            ),
            "live_post_processing_ocr_pass": False,
            "ocr_strategy_distinction": {
                "residual_ocr_text_evaluation": True,
                "live_post_processing_ocr_pass": False,
            },
            "summary": payload.summary.model_dump(mode="json"),
            "sensitive_meta_policy": payload.sensitive_meta_policy.value,
            "policy_applied": payload.policy_applied,
            "phi_region_confusion_matrix": asdict(
                evaluate_phi_region_confusion_matrix(video_ids=video_ids)
            ),
            "per_field_quality_metrics": _per_field_quality_metrics(
                video_ids=video_ids,
                pdf_ids=pdf_ids,
            ),
            "annotation_protocol_metadata": _annotation_protocol_payload(
                video_ids=video_ids
            ),
        },
        "simulated_lane": _simulated_lane_payload(),
    }
    _assert_manifest_is_phi_safe(manifest)
    return manifest


def _new_evaluation_run_context() -> _EvaluationRunContext:
    private_salt = secrets.token_hex(32)
    run_digest = hashlib.sha256(
        f"{private_salt}:{datetime.now(UTC).isoformat()}".encode("utf-8")
    ).hexdigest()[:32]
    return _EvaluationRunContext(
        evaluation_run_id=run_digest,
        private_hash_salt=private_salt,
    )


def _write_manifest(manifest: Mapping[str, object], *, output_dir: Path | None) -> Path:
    evaluation_run_id = str(manifest["evaluation_run_id"])
    destination_dir = output_dir or DEFAULT_MANIFEST_OUTPUT_DIR
    destination = destination_dir / f"run_{evaluation_run_id}.json"
    content = json.dumps(manifest, indent=2, sort_keys=True).encode("utf-8")
    return atomic_write_file(
        destination=destination,
        content=(content,),
        required_bytes=len(content),
        file_mode=0o600,
        dir_mode=0o700,
    )


def _performance_asset_entry(
    run: LxAnonymizerPerformanceRunPayload,
    *,
    context: _EvaluationRunContext,
) -> dict[str, object]:
    content_ref = _optional_hash_ref(
        context,
        namespace="content_hash",
        value=run.content_hash,
    )
    processed_ref = _optional_hash_ref(
        context,
        namespace="processed_hash",
        value=run.processed_hash,
    )
    return {
        "asset_ref": _hash_identifier(
            context,
            namespace="performance_source",
            value=run.source_sha256,
        ),
        "media_type": run.media_type,
        "iteration": run.iteration,
        "ok": run.ok,
        "source_size_bytes": run.source_size_bytes,
        "content_ref": content_ref,
        "processed_artifact_ref": processed_ref,
        "object_ref": _object_ref(run, context=context),
        "short_circuited": run.short_circuited,
        "error_type": run.error_type,
    }


def _quality_asset_catalog(
    results: Sequence[AnonymizationQualityResult],
    *,
    context: _EvaluationRunContext,
) -> list[dict[str, object]]:
    videos = _video_map(_video_ids_from_quality_results(results))
    pdfs = _pdf_map(_pdf_ids_from_quality_results(results))
    catalog: list[dict[str, object]] = []
    for result in results:
        if result.media_type == "video":
            asset_ref = _video_ref(videos.get(result.media_id), context=context)
        else:
            asset_ref = _pdf_ref(pdfs.get(result.media_id), context=context)
        catalog.append(
            {
                "asset_ref": asset_ref,
                "media_type": result.media_type,
                "status": result.status,
                "residual_phi_detected": result.residual_phi_detected,
                "checked_field_count": len(result.checked_fields),
                "warning_codes": result.warnings,
            }
        )
    return catalog


def _performance_resource_payload(
    runs: Sequence[LxAnonymizerPerformanceRunPayload],
    *,
    context: _EvaluationRunContext,
) -> dict[str, object]:
    return {
        "strategy": "existing_per_run_resource_counters_first_pass",
        "process_tree_tracking": (
            "not_measured; existing payload records parent process cpu and ru_maxrss"
        ),
        "io_metrics": "not_measured",
        "peak_allocation_monitoring": "parent_ru_maxrss_delta_only",
        "runs": [
            {
                "run_ref": _hash_identifier(
                    context,
                    namespace="performance_run",
                    value=f"{run.source_sha256}:{run.iteration}",
                ),
                "media_type": run.media_type,
                "ok": run.ok,
                "total_seconds": run.total_seconds,
                "import_seconds": run.import_seconds,
                "staging_seconds": run.staging_seconds,
                "anonymizer_seconds": run.anonymizer_seconds,
                "process_cpu_seconds": run.process_cpu_seconds,
                "max_rss_kib_delta": run.max_rss_kib_delta,
            }
            for run in runs
        ],
    }


def _video_descriptor_payload(
    *,
    video_ids: Sequence[int],
    context: _EvaluationRunContext,
) -> dict[str, object]:
    videos = list(_video_map(video_ids).values())
    frame_counts = [_positive_int_or_none(video.frame_count) for video in videos]
    durations = [_positive_float_or_none(video.duration) for video in videos]
    resolutions: dict[str, int] = {}
    codecs: dict[str, int] = {}
    processor_profiles: dict[str, int] = {}
    center_refs: set[str] = set()
    for video in videos:
        width = _positive_int_or_none(video.width)
        height = _positive_int_or_none(video.height)
        if width is not None and height is not None:
            key = f"{width}x{height}"
            resolutions[key] = resolutions.get(key, 0) + 1

        video_meta = getattr(video, "video_meta", None)
        ffmpeg_meta = getattr(video_meta, "ffmpeg_meta", None)
        codec_name = str(getattr(ffmpeg_meta, "codec_name", "") or "")
        if codec_name:
            codec_key = _safe_public_label(
                codec_name,
                namespace="codec",
                context=context,
            )
            codecs[codec_key] = codecs.get(codec_key, 0) + 1

        processor = getattr(video, "processor", None)
        processor_name = str(getattr(processor, "name", "") or "")
        if processor_name:
            processor_key = _safe_public_label(
                processor_name,
                namespace="processor_profile",
                context=context,
            )
            processor_profiles[processor_key] = (
                processor_profiles.get(processor_key, 0) + 1
            )

        center_id = getattr(video, "center_id", None)
        if center_id is not None:
            center_refs.add(
                _hash_identifier(context, namespace="center_id", value=str(center_id))
            )

    return {
        "video_count": len(videos),
        "frame_count": _numeric_summary(
            [value for value in frame_counts if value is not None]
        ),
        "duration_seconds": _numeric_summary(
            [value for value in durations if value is not None]
        ),
        "source_resolutions": _count_map_payload(resolutions),
        "codecs": _count_map_payload(codecs),
        "processor_profiles": _count_map_payload(processor_profiles),
        "participating_center_refs": sorted(center_refs),
        "descriptor_status": (
            "complete_for_available_video_model_fields" if videos else "no_video_assets"
        ),
    }


def _per_field_quality_metrics(
    *,
    video_ids: Sequence[int],
    pdf_ids: Sequence[int],
) -> list[dict[str, object]]:
    filters = Q()
    has_filter = False
    if video_ids:
        filters |= Q(validation_metric__video_id__in=video_ids)
        has_filter = True
    if pdf_ids:
        filters |= Q(validation_metric__pdf_id__in=pdf_ids)
        has_filter = True
    if not has_filter:
        return []

    counters: dict[str, _FieldCounter] = {}
    for metric in AnonymizationFieldMetric.objects.filter(filters).only(
        "field_name",
        "exact_match",
        "changed",
        "was_empty_after_validation",
    ):
        counter = counters.setdefault(metric.field_name, _FieldCounter())
        counter.support += 1
        if metric.exact_match:
            counter.exact_match_count += 1
        if metric.changed:
            counter.changed_count += 1
        if metric.was_empty_after_validation:
            counter.missing_after_validation_count += 1

    rows: list[dict[str, object]] = []
    for field_name in sorted(counters):
        counter = counters[field_name]
        exact_match_rate = (
            counter.exact_match_count / counter.support if counter.support else None
        )
        rows.append(
            {
                "field_name": field_name,
                "support": counter.support,
                "exact_match_count": counter.exact_match_count,
                "changed_count": counter.changed_count,
                "missing_after_validation_count": (
                    counter.missing_after_validation_count
                ),
                "exact_match_rate": exact_match_rate,
                "precision": None,
                "recall": None,
                "f1_score": None,
                "rate_note": ("field_tp_fp_fn_not_available_in_derived_metric_model"),
            }
        )
    return rows


def _annotation_protocol_payload(*, video_ids: Sequence[int]) -> dict[str, object]:
    qs = FrameBoxAnnotation.objects.filter(label__name=PHI_REGION_LABEL_NAME)
    if video_ids:
        qs = qs.filter(frame__video_id__in=video_ids)
    proposal_qs = qs.filter(
        information_source__name=PHI_REGION_INFORMATION_SOURCE_NAME,
        annotator=PHI_REGION_ANNOTATOR,
    )
    human_qs = qs.exclude(
        information_source__name=PHI_REGION_INFORMATION_SOURCE_NAME,
        annotator=PHI_REGION_ANNOTATOR,
    )
    return {
        "human_annotator_pool_count": (
            human_qs.exclude(annotator__isnull=True)
            .exclude(annotator="")
            .values("annotator")
            .distinct()
            .count()
        ),
        "proposal_source_count": proposal_qs.count(),
        "adjudication_rules": "not_recorded",
        "inter_annotator_agreement": None,
        "sampling_window": "not_recorded",
        "label_taxonomy_version": PHI_REGION_LABEL_NAME,
    }


def _environment_payload() -> dict[str, object]:
    return {
        "hardware_state": {
            "cpu_model": _cpu_model(),
            "cpu_count": os.cpu_count(),
            "total_system_ram_bytes": _total_ram_bytes(),
            "storage_volume_topology": _storage_volume_payload(),
            "gpu_availability": "not_measured",
        },
        "software_stack_ledger": {
            "python_version": sys.version.split()[0],
            "django_version": django.get_version(),
            "database_vendor": connection.vendor,
            "database_engine": _database_engine(),
            "git_commit": _git_commit(),
            "devenv_lock_sha256": _devenv_lock_hash(),
            "ffmpeg_version": _command_first_line(("ffmpeg", "-version")),
            "tesseract_version": _command_first_line(("tesseract", "--version")),
            "opencv_python_version": _package_version(
                ("opencv-python", "opencv-python-headless")
            ),
            "rapidocr_version": _package_version(("rapidocr", "rapidocr-onnxruntime")),
            "pytesseract_version": _package_version(("pytesseract",)),
        },
        "runtime_execution_context": {
            "thread_environment": _thread_environment(),
            "cache_state": "unspecified",
            "concurrency_pooling": "not_recorded",
        },
    }


def _privacy_payload() -> dict[str, object]:
    return {
        "sanitizer_version": MANIFEST_SANITIZER_VERSION,
        "asset_identifiers": "private_salt_sha256_refs",
        "raw_paths_included": False,
        "raw_filenames_included": False,
        "database_primary_keys_included": False,
        "raw_phi_values_included": False,
    }


def _simulated_lane_payload() -> dict[str, object]:
    return {
        "included": False,
        "reason": (
            "mTLS, Vault/KMS, LUKS, FIDO2, and network latency are intentionally "
            "decoupled from local execution measurements"
        ),
    }


def _video_ids_from_performance_runs(
    runs: Sequence[LxAnonymizerPerformanceRunPayload],
) -> tuple[int, ...]:
    return tuple(
        int(run.object_pk)
        for run in runs
        if run.ok and run.media_type == "video" and run.object_pk is not None
    )


def _video_ids_from_quality_results(
    results: Sequence[AnonymizationQualityResult],
) -> tuple[int, ...]:
    return tuple(result.media_id for result in results if result.media_type == "video")


def _pdf_ids_from_quality_results(
    results: Sequence[AnonymizationQualityResult],
) -> tuple[int, ...]:
    return tuple(result.media_id for result in results if result.media_type == "pdf")


def _video_map(video_ids: Sequence[int]) -> dict[int, VideoFile]:
    if not video_ids:
        return {}
    return {
        int(video.pk): video
        for video in VideoFile.objects.filter(pk__in=video_ids).select_related(
            "center",
            "processor",
            "video_meta__ffmpeg_meta",
        )
    }


def _pdf_map(pdf_ids: Sequence[int]) -> dict[int, RawPdfFile]:
    if not pdf_ids:
        return {}
    return {
        int(pdf.pk): pdf
        for pdf in RawPdfFile.objects.filter(pk__in=pdf_ids).select_related("center")
    }


def _video_ref(video: VideoFile | None, *, context: _EvaluationRunContext) -> str:
    if video is not None and video.video_hash:
        return _hash_identifier(context, namespace="video_hash", value=video.video_hash)
    return _hash_identifier(
        context, namespace="missing_video", value=secrets.token_hex(8)
    )


def _pdf_ref(pdf: RawPdfFile | None, *, context: _EvaluationRunContext) -> str:
    pdf_hash = str(getattr(pdf, "pdf_hash", "") or "") if pdf is not None else ""
    if pdf_hash:
        return _hash_identifier(context, namespace="pdf_hash", value=pdf_hash)
    return _hash_identifier(
        context, namespace="missing_pdf", value=secrets.token_hex(8)
    )


def _object_ref(
    run: LxAnonymizerPerformanceRunPayload,
    *,
    context: _EvaluationRunContext,
) -> str:
    if run.content_hash:
        return _hash_identifier(
            context, namespace="object_content", value=run.content_hash
        )
    if run.object_pk is not None:
        return _hash_identifier(
            context,
            namespace=f"object_pk:{run.media_type}",
            value=str(run.object_pk),
        )
    return _hash_identifier(
        context,
        namespace="object_missing",
        value=f"{run.source_sha256}:{run.iteration}",
    )


def _optional_hash_ref(
    context: _EvaluationRunContext,
    *,
    namespace: str,
    value: str,
) -> str | None:
    if not value:
        return None
    return _hash_identifier(context, namespace=namespace, value=value)


def _hash_identifier(
    context: _EvaluationRunContext,
    *,
    namespace: str,
    value: str,
) -> str:
    digest = hashlib.sha256(
        f"{context.private_hash_salt}\0{namespace}\0{value}".encode("utf-8")
    ).hexdigest()
    return f"evh_{digest[:32]}"


def _safe_public_label(
    value: str,
    *,
    namespace: str,
    context: _EvaluationRunContext,
) -> str:
    stripped = value.strip()
    if stripped and _SAFE_PUBLIC_LABEL_RE.match(stripped):
        return stripped
    return _hash_identifier(context, namespace=namespace, value=stripped)


def _numeric_summary(values: Sequence[int | float]) -> dict[str, object]:
    if not values:
        return {"count": 0, "total": 0, "min": None, "mean": None, "max": None}
    return {
        "count": len(values),
        "total": sum(values),
        "min": min(values),
        "mean": sum(values) / len(values),
        "max": max(values),
    }


def _count_map_payload(counts: Mapping[str, int]) -> list[dict[str, object]]:
    return [
        {"value": value, "count": counts[value]}
        for value in sorted(counts, key=lambda item: (counts[item], item), reverse=True)
    ]


def _positive_int_or_none(value: object) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        result = value
    elif isinstance(value, float):
        result = int(value)
    elif isinstance(value, str):
        try:
            result = int(value)
        except ValueError:
            return None
    else:
        return None
    return result if result >= 0 else None


def _positive_float_or_none(value: object) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int | float):
        result = float(value)
    elif isinstance(value, str):
        try:
            result = float(value)
        except ValueError:
            return None
    else:
        return None
    return result if result >= 0 else None


def _quality_cohort_status(payload: AnonymizationQualityPayload) -> str:
    failed_count = payload.summary.status_counts.get("failed_or_lost", 0)
    not_measurable_count = payload.summary.status_counts.get("not_measurable", 0)
    if failed_count:
        return "contains_failed_or_lost_media"
    if not_measurable_count:
        return "contains_not_measurable_media"
    return "evaluated"


def _cohort_status(*, failed_runs: int) -> str:
    return "contains_failed_runs" if failed_runs else "evaluated"


def _cpu_model() -> str:
    cpuinfo = Path("/proc/cpuinfo")
    try:
        for line in cpuinfo.read_text(encoding="utf-8").splitlines():
            if line.startswith("model name"):
                return line.split(":", 1)[1].strip()
    except OSError:
        pass
    return platform.processor() or "unknown"


def _total_ram_bytes() -> int | None:
    if not hasattr(os, "sysconf"):
        return None
    try:
        page_size = os.sysconf("SC_PAGE_SIZE")
        physical_pages = os.sysconf("SC_PHYS_PAGES")
    except (OSError, ValueError):
        return None
    return page_size * physical_pages


def _storage_volume_payload() -> dict[str, object]:
    try:
        usage = os.statvfs(str(DEFAULT_MANIFEST_OUTPUT_DIR.parent))
    except OSError:
        return {"measured": False}
    block_size = usage.f_frsize
    total = usage.f_blocks * block_size
    free = usage.f_bavail * block_size
    return {
        "measured": True,
        "volume_ref": "manifest_output_volume",
        "total_bytes": total,
        "available_bytes": free,
    }


def _database_engine() -> str:
    database_config = cast(Mapping[str, object], settings.DATABASES["default"])
    return str(database_config.get("ENGINE", ""))


def _git_commit() -> str:
    return _command_first_line(("git", "rev-parse", "HEAD"), cwd=BASE_DIR)


def _devenv_lock_hash() -> str:
    lock_path = BASE_DIR / "devenv.lock"
    if not lock_path.exists():
        return ""
    try:
        return sha256_file(lock_path)
    except OSError:
        return ""


def _command_first_line(command: Sequence[str], *, cwd: Path | None = None) -> str:
    try:
        completed = subprocess.run(
            list(command),
            cwd=str(cwd) if cwd is not None else None,
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    output = completed.stdout or completed.stderr
    first_line = output.splitlines()[0].strip() if output.splitlines() else ""
    return first_line[:240]


def _package_version(package_names: Sequence[str]) -> str:
    for package_name in package_names:
        try:
            return importlib.metadata.version(package_name)
        except importlib.metadata.PackageNotFoundError:
            continue
    return ""


def _thread_environment() -> dict[str, str]:
    keys = (
        "OMP_NUM_THREADS",
        "OPENBLAS_NUM_THREADS",
        "MKL_NUM_THREADS",
        "NUMEXPR_NUM_THREADS",
        "ONNXRUNTIME_THREAD_COUNT",
    )
    return {key.lower(): os.environ[key] for key in keys if key in os.environ}


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _assert_manifest_is_phi_safe(value: object, *, path: str = "$") -> None:
    if isinstance(value, Mapping):
        mapping = cast(Mapping[object, object], value)
        for key, child in mapping.items():
            _assert_manifest_is_phi_safe(child, path=f"{path}.{key}")
        return
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        sequence = cast(Sequence[object], value)
        for index, child in enumerate(sequence):
            _assert_manifest_is_phi_safe(child, path=f"{path}[{index}]")
        return
    if isinstance(value, str) and _unsafe_manifest_string(value):
        raise ValueError(f"Unsafe manifest string at {path}")


def _unsafe_manifest_string(value: str) -> bool:
    if _UNSAFE_PATH_RE.search(value):
        return True
    return _UNSAFE_MEDIA_FILENAME_RE.search(value) is not None
