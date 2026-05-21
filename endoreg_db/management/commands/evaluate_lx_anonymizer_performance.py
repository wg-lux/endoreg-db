from __future__ import annotations

import csv
import io
import json
import logging
import os
import resource
import time
from argparse import ArgumentParser
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Literal, Mapping, cast

from django.core.management.base import BaseCommand, CommandError

from endoreg_db.models import EndoscopyProcessor
from endoreg_db.services.report_import import ReportImportService
from endoreg_db.services.video_import import VideoImportService
from endoreg_db.utils import paths as path_utils
from endoreg_db.utils.file_operations import (
    atomic_copy_file,
    atomic_write_file,
    ensure_directory,
    safe_unlink_file,
    sha256_file,
)

logger = logging.getLogger(__name__)

MediaType = Literal["video", "report"]

VIDEO_EXTENSIONS = {".avi", ".m4v", ".mkv", ".mov", ".mp4", ".mpg", ".mpeg"}
REPORT_EXTENSIONS = {".pdf"}
REPORT_BYPASS_EXTENSIONS = {".txt"}


def _roi_is_configured(roi: dict[str, int | None] | None) -> bool:
    if roi is None:
        return False
    required_keys = {"x", "y", "width", "height"}
    if not required_keys.issubset(roi):
        return False
    x = roi["x"]
    y = roi["y"]
    width = roi["width"]
    height = roi["height"]
    coordinates_are_valid = (
        isinstance(x, int)
        and isinstance(y, int)
        and isinstance(width, int)
        and isinstance(height, int)
        and x >= 0
        and y >= 0
        and width > 0
        and height > 0
    )
    if not coordinates_are_valid:
        return False

    image_width = roi.get("image_width")
    image_height = roi.get("image_height")
    image_dimensions_are_valid = (
        image_width is None or isinstance(image_width, int) and image_width > 0
    ) and (image_height is None or isinstance(image_height, int) and image_height > 0)
    return image_dimensions_are_valid


@dataclass
class EvaluationRun:
    source_path: str
    staged_path: str
    media_type: str
    iteration: int
    source_size_bytes: int
    source_sha256: str
    ok: bool
    total_seconds: float
    import_seconds: float
    staging_seconds: float
    anonymizer_seconds: float | None
    process_cpu_seconds: float
    max_rss_kib_delta: int
    object_model: str = ""
    object_pk: int | None = None
    content_hash: str = ""
    processed_hash: str = ""
    raw_file_name: str = ""
    processed_file_name: str = ""
    short_circuited: bool = False
    error_type: str = ""
    error: str = ""


@dataclass
class TimedCallRecorder:
    durations: list[float] = field(default_factory=list)

    def time_call(self, callback: Callable[..., Any], *args, **kwargs) -> Any:
        start = time.perf_counter()
        try:
            return callback(*args, **kwargs)
        finally:
            self.durations.append(time.perf_counter() - start)


class TimedAnonymizer:
    def __init__(self, wrapped: object, recorder: TimedCallRecorder):
        self._wrapped = wrapped
        self._recorder = recorder

    def __getattr__(self, name: str) -> Any:
        return getattr(self._wrapped, name)

    def anonymize_video(self, *args, **kwargs) -> Any:
        return self._recorder.time_call(
            getattr(self._wrapped, "anonymize_video"),
            *args,
            **kwargs,
        )

    def anonymize_report(self, *args, **kwargs) -> Any:
        return self._recorder.time_call(
            getattr(self._wrapped, "anonymize_report"),
            *args,
            **kwargs,
        )


class Command(BaseCommand):
    help = (
        "Evaluate lx_anonymizer performance through endoreg_db's canonical "
        "VideoImportService and ReportImportService pipelines."
    )

    def add_arguments(self, parser: ArgumentParser) -> None:
        parser.add_argument(
            "paths",
            nargs="*",
            help=(
                "Files or directories to evaluate. Directories are scanned for "
                "media files. Report performance evaluation only accepts PDFs."
            ),
        )
        parser.add_argument(
            "--input-dir",
            action="append",
            default=[],
            help="Additional directory to scan for input media.",
        )
        parser.add_argument(
            "--media-type",
            choices=("auto", "video", "report"),
            default="auto",
            help="Force all inputs to one media type, or infer from file extension.",
        )
        parser.add_argument(
            "--recursive",
            action="store_true",
            help="Recursively scan input directories.",
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=0,
            help="Maximum number of discovered files to evaluate. 0 means no limit.",
        )
        parser.add_argument(
            "--repeat",
            type=int,
            default=1,
            help="Number of times to run each input.",
        )
        parser.add_argument(
            "--retry",
            action="store_true",
            help=(
                "Force re-processing through the import services. Without this, "
                "duplicates may short-circuit through ProcessingHistory."
            ),
        )
        parser.add_argument(
            "--center-name",
            default="university_hospital_wuerzburg",
            help="Center name passed to import services.",
        )
        parser.add_argument(
            "--processor-name",
            default="olympus_cv_1500",
            help="Processor name passed to VideoImportService.",
        )
        parser.add_argument(
            "--load-reference-data",
            action="store_true",
            help="Load endoreg_db reference data before evaluating.",
        )
        parser.add_argument(
            "--keep-staged-inputs",
            action="store_true",
            help="Keep evaluator plaintext staging copies after each run.",
        )
        parser.add_argument(
            "--continue-on-error",
            action="store_true",
            help="Continue evaluating remaining files after a failure.",
        )
        parser.add_argument(
            "--json-output",
            type=str,
            default="",
            help="Write full JSON results to this path.",
        )
        parser.add_argument(
            "--csv-output",
            type=str,
            default="",
            help="Write flat per-run CSV results to this path.",
        )
        parser.add_argument(
            "--json",
            action="store_true",
            help="Emit full JSON results to stdout instead of the compact text summary.",
        )

    def handle(self, *args, **options) -> None:
        repeat = int(options["repeat"])
        if repeat < 1:
            raise CommandError("--repeat must be >= 1")

        limit = int(options["limit"])
        if limit < 0:
            raise CommandError("--limit must be >= 0")

        if options["load_reference_data"]:
            from endoreg_db.helpers.data_load_orchestrator import (
                load_all_reference_data,
            )

            load_all_reference_data()

        inputs = self._discover_inputs(
            paths=[*(options["paths"] or []), *(options["input_dir"] or [])],
            forced_media_type=options["media_type"],
            recursive=bool(options["recursive"]),
            limit=limit,
        )
        skipped_video_count = 0
        inputs, skipped_video_count = self._exclude_video_inputs_without_roi(
            inputs=inputs,
            processor_name=str(options["processor_name"]),
        )
        if not inputs:
            detail = (
                " Video inputs were excluded because the selected processor has "
                "missing or invalid ROI data."
                if skipped_video_count
                else ""
            )
            raise CommandError(f"No supported video/report inputs were found.{detail}")

        if repeat > 1 and not options["retry"]:
            logger.warning(
                "repeat=%s without --retry can benchmark duplicate short-circuiting, "
                "not lx_anonymizer execution.",
                repeat,
            )

        run_results: list[EvaluationRun] = []
        for source_path, media_type in inputs:
            for iteration in range(1, repeat + 1):
                result = self._run_one(
                    source_path=source_path,
                    media_type=media_type,
                    iteration=iteration,
                    center_name=str(options["center_name"]),
                    processor_name=str(options["processor_name"]),
                    retry=bool(options["retry"]),
                    keep_staged_inputs=bool(options["keep_staged_inputs"]),
                )
                run_results.append(result)
                if not result.ok and not options["continue_on_error"]:
                    break
            if (
                run_results
                and not run_results[-1].ok
                and not options["continue_on_error"]
            ):
                break

        payload: dict[str, object] = {
            "summary": self._summarize(run_results),
            "runs": [asdict(result) for result in run_results],
        }

        if options["json_output"]:
            self._write_json(Path(options["json_output"]), payload)
        if options["csv_output"]:
            self._write_csv(Path(options["csv_output"]), run_results)

        if options["json"]:
            self.stdout.write(json.dumps(payload, indent=2, sort_keys=True))
        else:
            self._write_text_summary(payload)

        if any(not result.ok for result in run_results):
            raise CommandError("One or more evaluation runs failed.")

    def _discover_inputs(
        self,
        *,
        paths: list[str],
        forced_media_type: str,
        recursive: bool,
        limit: int,
    ) -> list[tuple[Path, MediaType]]:
        discovered: list[tuple[Path, MediaType]] = []

        for raw_path in paths:
            path = Path(raw_path).expanduser().resolve()
            if not path.exists():
                raise CommandError(f"Input path does not exist: {path}")
            candidates: Iterable[Path]
            if path.is_dir():
                candidates = path.rglob("*") if recursive else path.iterdir()
            else:
                candidates = [path]

            for candidate in sorted(candidates):
                if not candidate.is_file() or candidate.is_symlink():
                    continue
                media_type = self._media_type_for_path(candidate, forced_media_type)
                if media_type is None:
                    continue
                discovered.append((candidate, media_type))
                if limit and len(discovered) >= limit:
                    return discovered

        return discovered

    def _exclude_video_inputs_without_roi(
        self,
        *,
        inputs: list[tuple[Path, MediaType]],
        processor_name: str,
    ) -> tuple[list[tuple[Path, MediaType]], int]:
        video_inputs = [
            (source_path, media_type)
            for source_path, media_type in inputs
            if media_type == "video"
        ]
        if not video_inputs:
            return inputs, 0
        if self._processor_has_evaluable_video_roi(processor_name):
            return inputs, 0

        skipped_count = len(video_inputs)
        logger.warning(
            "Excluding %s video input(s) from lx_anonymizer evaluation because "
            "processor %r has missing or invalid ROI data.",
            skipped_count,
            processor_name,
        )
        return [
            (source_path, media_type)
            for source_path, media_type in inputs
            if media_type != "video"
        ], skipped_count

    @staticmethod
    def _processor_has_evaluable_video_roi(processor_name: str) -> bool:
        if not processor_name:
            return False
        try:
            processor = EndoscopyProcessor.get_by_name(processor_name)
        except EndoscopyProcessor.DoesNotExist:
            return False

        if not _roi_is_configured(processor.get_roi_endoscope_image()):
            return False

        sensitive_rois = processor.get_sensitive_rois()
        configured_sensitive_rois = [
            roi for roi in sensitive_rois.values() if _roi_is_configured(roi)
        ]
        invalid_sensitive_rois = [
            roi
            for roi in sensitive_rois.values()
            if roi is not None and not _roi_is_configured(roi)
        ]
        return bool(configured_sensitive_rois) and not invalid_sensitive_rois

    @staticmethod
    def _media_type_for_path(path: Path, forced_media_type: str) -> MediaType | None:
        suffix = path.suffix.lower()
        if suffix in REPORT_BYPASS_EXTENSIONS:
            if forced_media_type == "report":
                raise CommandError(
                    "Text report inputs bypass lx_anonymizer in the report import "
                    f"pipeline and cannot be used for performance evaluation: {path}"
                )
            return None
        if forced_media_type == "video":
            return "video"
        if forced_media_type == "report":
            return "report"
        if suffix in VIDEO_EXTENSIONS:
            return "video"
        if suffix in REPORT_EXTENSIONS:
            return "report"
        return None

    def _run_one(
        self,
        *,
        source_path: Path,
        media_type: MediaType,
        iteration: int,
        center_name: str,
        processor_name: str,
        retry: bool,
        keep_staged_inputs: bool,
    ) -> EvaluationRun:
        source_size = source_path.stat().st_size
        source_hash = sha256_file(source_path)
        staged_path = Path("")
        staging_seconds = 0.0
        import_seconds = 0.0
        anonymizer_seconds: float | None = None
        process_cpu_start = time.process_time()
        rss_start = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        total_start = time.perf_counter()

        try:
            stage_start = time.perf_counter()
            staged_path = self._stage_input(source_path, iteration)
            staging_seconds = time.perf_counter() - stage_start

            recorder = TimedCallRecorder()
            import_start = time.perf_counter()
            imported = self._import_with_timing(
                staged_path=staged_path,
                media_type=media_type,
                center_name=center_name,
                processor_name=processor_name,
                retry=retry,
                recorder=recorder,
            )
            import_seconds = time.perf_counter() - import_start
            anonymizer_seconds = sum(recorder.durations) if recorder.durations else 0.0

            return EvaluationRun(
                source_path=source_path.as_posix(),
                staged_path=staged_path.as_posix(),
                media_type=media_type,
                iteration=iteration,
                source_size_bytes=source_size,
                source_sha256=source_hash,
                ok=True,
                total_seconds=time.perf_counter() - total_start,
                import_seconds=import_seconds,
                staging_seconds=staging_seconds,
                anonymizer_seconds=anonymizer_seconds,
                process_cpu_seconds=time.process_time() - process_cpu_start,
                max_rss_kib_delta=(
                    resource.getrusage(resource.RUSAGE_SELF).ru_maxrss - rss_start
                ),
                object_model=imported.__class__.__name__,
                object_pk=getattr(imported, "pk", None),
                content_hash=self._content_hash(imported),
                processed_hash=self._processed_hash(imported),
                raw_file_name=self._field_name(imported, "raw_file")
                or self._field_name(imported, "file"),
                processed_file_name=self._field_name(imported, "processed_file"),
                short_circuited=anonymizer_seconds == 0.0,
            )
        except Exception as exc:
            logger.exception(
                "lx_anonymizer evaluation failed for %s iteration=%s",
                source_path,
                iteration,
            )
            return EvaluationRun(
                source_path=source_path.as_posix(),
                staged_path=staged_path.as_posix() if staged_path else "",
                media_type=media_type,
                iteration=iteration,
                source_size_bytes=source_size,
                source_sha256=source_hash,
                ok=False,
                total_seconds=time.perf_counter() - total_start,
                import_seconds=import_seconds,
                staging_seconds=staging_seconds,
                anonymizer_seconds=anonymizer_seconds,
                process_cpu_seconds=time.process_time() - process_cpu_start,
                max_rss_kib_delta=(
                    resource.getrusage(resource.RUSAGE_SELF).ru_maxrss - rss_start
                ),
                error_type=exc.__class__.__name__,
                error=str(exc),
            )
        finally:
            if staged_path and not keep_staged_inputs:
                safe_unlink_file(staged_path, missing_ok=True)

    def _stage_input(self, source_path: Path, iteration: int) -> Path:
        paths = path_utils.EndoregPathsModel.from_environment()
        staging_dir = ensure_directory(paths.transcoding / "lx_anonymizer_eval")
        staged_name = (
            f"eval_{os.getpid()}_{iteration}_{time.time_ns()}_{source_path.name}"
        )
        return atomic_copy_file(
            source=source_path,
            destination=staging_dir / staged_name,
            preserve_metadata=True,
        )

    def _import_with_timing(
        self,
        *,
        staged_path: Path,
        media_type: MediaType,
        center_name: str,
        processor_name: str,
        retry: bool,
        recorder: TimedCallRecorder,
    ) -> object:
        result: object | None
        if media_type == "video":
            video_service = VideoImportService()
            video_service.anonymizer = cast(
                Any,
                TimedAnonymizer(video_service.anonymizer, recorder),
            )
            result = video_service.import_and_anonymize(
                file_path=staged_path,
                center_name=center_name,
                processor_name=processor_name,
                retry=retry,
            )
        else:
            report_service = ReportImportService()
            report_service.anonymizer = cast(
                Any,
                TimedAnonymizer(report_service.anonymizer, recorder),
            )
            result = report_service.import_and_anonymize(
                file_path=staged_path,
                center_name=center_name,
                retry=retry,
            )

        if result is None:
            raise RuntimeError(f"{media_type} import returned no object")
        return result

    @staticmethod
    def _field_name(instance: object, field_name: str) -> str:
        field_file = getattr(instance, field_name, None)
        return str(getattr(field_file, "name", "") or "")

    @staticmethod
    def _content_hash(instance: object) -> str:
        return str(
            getattr(instance, "video_hash", "")
            or getattr(instance, "pdf_hash", "")
            or ""
        )

    @staticmethod
    def _processed_hash(instance: object) -> str:
        return str(
            getattr(instance, "processed_video_hash", "")
            or getattr(instance, "processed_pdf_hash", "")
            or ""
        )

    @staticmethod
    def _summarize(results: list[EvaluationRun]) -> dict[str, object]:
        ok_results = [result for result in results if result.ok]
        failed_results = [result for result in results if not result.ok]
        anonymizer_durations = [
            result.anonymizer_seconds
            for result in ok_results
            if result.anonymizer_seconds is not None
        ]
        import_durations = [result.import_seconds for result in ok_results]
        total_durations = [result.total_seconds for result in ok_results]
        return {
            "total_runs": len(results),
            "ok_runs": len(ok_results),
            "failed_runs": len(failed_results),
            "short_circuited_runs": sum(
                1 for result in ok_results if result.short_circuited
            ),
            "total_seconds": sum(total_durations),
            "import_seconds": Command._duration_stats(import_durations),
            "anonymizer_seconds": Command._duration_stats(anonymizer_durations),
            "end_to_end_seconds": Command._duration_stats(total_durations),
        }

    @staticmethod
    def _duration_stats(values: list[float]) -> dict[str, float | int]:
        if not values:
            return {"count": 0, "min": 0.0, "mean": 0.0, "max": 0.0, "p95": 0.0}
        sorted_values = sorted(values)
        return {
            "count": len(values),
            "min": sorted_values[0],
            "mean": sum(sorted_values) / len(sorted_values),
            "max": sorted_values[-1],
            "p95": Command._percentile(sorted_values, 0.95),
        }

    @staticmethod
    def _percentile(sorted_values: list[float], percentile: float) -> float:
        if not sorted_values:
            return 0.0
        index = min(
            len(sorted_values) - 1,
            max(0, round((len(sorted_values) - 1) * percentile)),
        )
        return sorted_values[index]

    def _write_json(self, destination: Path, payload: Mapping[str, object]) -> None:
        encoded = json.dumps(payload, indent=2, sort_keys=True).encode("utf-8")
        atomic_write_file(
            destination=destination,
            content=[encoded],
            required_bytes=len(encoded),
        )

    def _write_csv(self, destination: Path, results: list[EvaluationRun]) -> None:
        buffer = io.StringIO()
        fieldnames = list(asdict(results[0]).keys()) if results else []
        writer = csv.DictWriter(buffer, fieldnames=fieldnames)
        writer.writeheader()
        for result in results:
            writer.writerow(asdict(result))
        encoded = buffer.getvalue().encode("utf-8")
        atomic_write_file(
            destination=destination,
            content=[encoded],
            required_bytes=len(encoded),
        )

    def _write_text_summary(self, payload: Mapping[str, object]) -> None:
        summary = payload["summary"]
        assert isinstance(summary, dict)
        self.stdout.write(self.style.SUCCESS("lx_anonymizer evaluation complete"))
        self.stdout.write(
            "runs={total_runs} ok={ok_runs} failed={failed_runs} "
            "short_circuited={short_circuited_runs}".format(**summary)
        )
        anonymizer_stats = summary.get("anonymizer_seconds", {})
        import_stats = summary.get("import_seconds", {})
        if isinstance(anonymizer_stats, dict):
            self.stdout.write(
                "anonymizer_seconds: mean={mean:.3f} p95={p95:.3f} max={max:.3f}".format(
                    **anonymizer_stats
                )
            )
        if isinstance(import_stats, dict):
            self.stdout.write(
                "import_seconds: mean={mean:.3f} p95={p95:.3f} max={max:.3f}".format(
                    **import_stats
                )
            )
