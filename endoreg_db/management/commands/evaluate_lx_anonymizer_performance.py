from __future__ import annotations

import csv
import io
import logging
import os
import resource
import time
from collections.abc import Callable, Iterable
from pathlib import Path
from types import NoneType
from typing import (
    Literal,
    ParamSpec,
    Protocol,
    TypeAlias,
    TypeVar,
    TypedDict,
    Unpack,
    cast,
)

from django.core.management.base import BaseCommand, CommandError, CommandParser
from django.db.models.fields.files import FieldFile
from lx_dtypes.models.contracts import (
    LX_ANONYMIZER_PERFORMANCE_CSV_FIELDNAMES,
    LxAnonymizerDurationStatsPayload,
    LxAnonymizerPerformanceMediaType,
    LxAnonymizerPerformancePayload,
    LxAnonymizerPerformanceRunPayload,
    LxAnonymizerPerformanceSummaryPayload,
    dump_lx_anonymizer_performance_run_csv_row,
)
from lx_dtypes.models.contracts.endoscopy_processor import (
    RoiBoxCore,
    roi_box_or_none_from_object,
)
from pydantic import ValidationError

from endoreg_db.models import EndoscopyProcessor
from endoreg_db.import_files.context.import_context import ImportContext
from endoreg_db.models.media.pdf.raw_pdf import RawPdfFile
from endoreg_db.models.media.video.video_file import VideoFile
from endoreg_db.services.report_import import ReportImportService
from endoreg_db.services.evaluation_manifest import (
    write_performance_evaluation_manifest,
)
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

JsonNull: TypeAlias = NoneType
MediaType: TypeAlias = LxAnonymizerPerformanceMediaType
ForcedMediaType: TypeAlias = Literal["auto", "video", "report"]
ProcessorRoi: TypeAlias = RoiBoxCore | dict[str, int | JsonNull]
ImportedMedia: TypeAlias = VideoFile | RawPdfFile
TimedParameters = ParamSpec("TimedParameters")
TimedReturn = TypeVar("TimedReturn")

VIDEO_EXTENSIONS = {".avi", ".m4v", ".mkv", ".mov", ".mp4", ".mpg", ".mpeg"}
REPORT_EXTENSIONS = {".pdf"}
REPORT_BYPASS_EXTENSIONS = {".txt"}


class _ProcessorWithRois(Protocol):
    def get_roi_endoscope_image(self) -> ProcessorRoi | JsonNull: ...

    def get_sensitive_rois(self) -> dict[str, ProcessorRoi | JsonNull]: ...


class _ProcessorRegistry(Protocol):
    def get_by_name(self, name: str) -> _ProcessorWithRois: ...


class _VideoAnonymizer(Protocol):
    def anonymize_video(self, ctx: ImportContext) -> ImportContext: ...


class _ReportAnonymizer(Protocol):
    def anonymize_report(self, ctx: ImportContext) -> ImportContext: ...


class _TimedVideoImportService(Protocol):
    anonymizer: _VideoAnonymizer

    def import_and_anonymize(
        self,
        file_path: Path,
        center_name: str,
        processor_name: str,
        retry: bool,
    ) -> VideoFile | JsonNull: ...


class _TimedReportImportService(Protocol):
    anonymizer: _ReportAnonymizer

    def import_and_anonymize(
        self,
        file_path: Path,
        center_name: str,
        retry: bool,
    ) -> RawPdfFile | JsonNull: ...


class _NamedFieldFile(Protocol):
    name: str | JsonNull


class _VideoEvaluationMedia(Protocol):
    pk: int | JsonNull
    video_hash: str
    processed_video_hash: str | JsonNull
    raw_file: FieldFile
    processed_file: FieldFile


class _ReportEvaluationMedia(Protocol):
    pk: int | JsonNull
    pdf_hash: str
    file: FieldFile
    processed_file: FieldFile


def _roi_is_configured(roi: ProcessorRoi | JsonNull) -> bool:
    if roi is None:
        return False
    try:
        roi_box = roi_box_or_none_from_object(roi)
    except ValidationError:
        return False
    if roi_box is None:
        return False
    x = roi_box.x
    y = roi_box.y
    width = roi_box.width
    height = roi_box.height
    coordinates_are_valid = x >= 0 and y >= 0 and width > 0 and height > 0
    if not coordinates_are_valid:
        return False

    image_width = getattr(roi_box, "image_width", None)
    image_height = getattr(roi_box, "image_height", None)
    image_dimensions_are_valid = (image_width is None or image_width > 0) and (
        image_height is None or image_height > 0
    )
    return image_dimensions_are_valid


class TimedCallRecorder:
    def __init__(self) -> None:
        self.durations: list[float] = []

    def time_call(
        self,
        callback: Callable[TimedParameters, TimedReturn],
        *args: TimedParameters.args,
        **kwargs: TimedParameters.kwargs,
    ) -> TimedReturn:
        start = time.perf_counter()
        try:
            return callback(*args, **kwargs)
        finally:
            self.durations.append(time.perf_counter() - start)


class TimedVideoAnonymizer:
    def __init__(
        self,
        wrapped: _VideoAnonymizer,
        recorder: TimedCallRecorder,
    ) -> None:
        self._wrapped = wrapped
        self._recorder = recorder

    def anonymize_video(self, ctx: ImportContext) -> ImportContext:
        return self._recorder.time_call(self._wrapped.anonymize_video, ctx)


class TimedReportAnonymizer:
    def __init__(
        self,
        wrapped: _ReportAnonymizer,
        recorder: TimedCallRecorder,
    ) -> None:
        self._wrapped = wrapped
        self._recorder = recorder

    def anonymize_report(self, ctx: ImportContext) -> ImportContext:
        return self._recorder.time_call(self._wrapped.anonymize_report, ctx)


class PerformanceCommandOptions(TypedDict):
    paths: list[str]
    input_dir: list[str]
    media_type: ForcedMediaType
    recursive: bool
    limit: int
    repeat: int
    retry: bool
    center_name: str
    processor_name: str
    load_reference_data: bool
    keep_staged_inputs: bool
    continue_on_error: bool
    json_output: str
    csv_output: str
    generate_manifest: bool
    manifest_output_dir: str
    json: bool


class Command(BaseCommand):
    help = (
        "Evaluate lx_anonymizer performance through endoreg_db's canonical "
        "VideoImportService and ReportImportService pipelines."
    )

    def add_arguments(self, parser: CommandParser) -> None:
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
            "--generate-manifest",
            action="store_true",
            help=(
                "Write a PHI-safe unified evaluation manifest for this run to "
                "/data/results/manifests by default."
            ),
        )
        parser.add_argument(
            "--manifest-output-dir",
            type=str,
            default="",
            help=(
                "Override the evaluation manifest output directory. Intended for "
                "controlled test or export environments."
            ),
        )
        parser.add_argument(
            "--json",
            action="store_true",
            help="Emit full JSON results to stdout instead of the compact text summary.",
        )

    def handle(
        self,
        *args: str,
        **options: Unpack[PerformanceCommandOptions],
    ) -> None:
        repeat = options["repeat"]
        if repeat < 1:
            raise CommandError("--repeat must be >= 1")

        limit = options["limit"]
        if limit < 0:
            raise CommandError("--limit must be >= 0")

        if options["load_reference_data"]:
            from endoreg_db.helpers.data_load_orchestrator import (
                load_all_reference_data,
            )

            load_all_reference_data()

        inputs = self._discover_inputs(
            paths=[*options["paths"], *options["input_dir"]],
            forced_media_type=options["media_type"],
            recursive=options["recursive"],
            limit=limit,
        )
        skipped_video_count = 0
        inputs, skipped_video_count = self._exclude_video_inputs_without_roi(
            inputs=inputs,
            processor_name=options["processor_name"],
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

        run_results: list[LxAnonymizerPerformanceRunPayload] = []
        for source_path, media_type in inputs:
            for iteration in range(1, repeat + 1):
                result = self._run_one(
                    source_path=source_path,
                    media_type=media_type,
                    iteration=iteration,
                    center_name=options["center_name"],
                    processor_name=options["processor_name"],
                    retry=options["retry"],
                    keep_staged_inputs=options["keep_staged_inputs"],
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

        payload = LxAnonymizerPerformancePayload(
            summary=self._summarize(run_results),
            runs=run_results,
        )

        if options["json_output"]:
            self._write_json(Path(options["json_output"]), payload)
        if options["csv_output"]:
            self._write_csv(Path(options["csv_output"]), run_results)
        if options["generate_manifest"]:
            manifest_path = write_performance_evaluation_manifest(
                payload,
                processor_name=options["processor_name"],
                center_name=options["center_name"],
                output_dir=(
                    Path(options["manifest_output_dir"])
                    if options["manifest_output_dir"]
                    else None
                ),
            )
            self.stderr.write(f"evaluation_manifest: {manifest_path}")

        if options["json"]:
            self.stdout.write(payload.model_dump_json(indent=2))
        else:
            self._write_text_summary(payload)

        if any(not result.ok for result in run_results):
            raise CommandError("One or more evaluation runs failed.")

    def _discover_inputs(
        self,
        *,
        paths: list[str],
        forced_media_type: ForcedMediaType,
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
            processor_registry = cast(_ProcessorRegistry, EndoscopyProcessor)
            processor = processor_registry.get_by_name(processor_name)
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
    def _media_type_for_path(
        path: Path,
        forced_media_type: ForcedMediaType,
    ) -> MediaType | JsonNull:
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
    ) -> LxAnonymizerPerformanceRunPayload:
        source_size = source_path.stat().st_size
        source_hash = sha256_file(source_path)
        staged_path: Path | JsonNull = None
        staging_seconds = 0.0
        import_seconds = 0.0
        anonymizer_seconds: float | JsonNull = None
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

            return LxAnonymizerPerformanceRunPayload(
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
                object_pk=self._media_pk(imported),
                content_hash=self._content_hash(imported),
                processed_hash=self._processed_hash(imported),
                raw_file_name=self._raw_file_name(imported),
                processed_file_name=self._processed_file_name(imported),
                short_circuited=anonymizer_seconds == 0.0,
            )
        except Exception as exc:
            logger.exception(
                "lx_anonymizer evaluation failed for %s iteration=%s",
                source_path,
                iteration,
            )
            return LxAnonymizerPerformanceRunPayload(
                source_path=source_path.as_posix(),
                staged_path=staged_path.as_posix() if staged_path is not None else "",
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
            if staged_path is not None and not keep_staged_inputs:
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
    ) -> ImportedMedia:
        if media_type == "video":
            video_service = cast(_TimedVideoImportService, VideoImportService())
            video_service.anonymizer = TimedVideoAnonymizer(
                video_service.anonymizer,
                recorder,
            )
            video_result = video_service.import_and_anonymize(
                file_path=staged_path,
                center_name=center_name,
                processor_name=processor_name,
                retry=retry,
            )
            if video_result is None:
                raise RuntimeError("video import returned no media instance")
            return video_result

        report_service = cast(_TimedReportImportService, ReportImportService())
        report_service.anonymizer = TimedReportAnonymizer(
            report_service.anonymizer,
            recorder,
        )
        report_result = report_service.import_and_anonymize(
            file_path=staged_path,
            center_name=center_name,
            retry=retry,
        )
        if report_result is None:
            raise RuntimeError("report import returned no media instance")
        return report_result

    @staticmethod
    def _field_file_name(field_file: FieldFile) -> str:
        typed_field_file = cast(_NamedFieldFile, field_file)
        return str(typed_field_file.name or "")

    @staticmethod
    def _media_pk(instance: ImportedMedia) -> int | JsonNull:
        if isinstance(instance, VideoFile):
            return cast(_VideoEvaluationMedia, instance).pk
        return cast(_ReportEvaluationMedia, instance).pk

    @staticmethod
    def _raw_file_name(instance: ImportedMedia) -> str:
        if isinstance(instance, VideoFile):
            video = cast(_VideoEvaluationMedia, instance)
            return Command._field_file_name(video.raw_file)
        report = cast(_ReportEvaluationMedia, instance)
        return Command._field_file_name(report.file)

    @staticmethod
    def _processed_file_name(instance: ImportedMedia) -> str:
        if isinstance(instance, VideoFile):
            video = cast(_VideoEvaluationMedia, instance)
            return Command._field_file_name(video.processed_file)
        report = cast(_ReportEvaluationMedia, instance)
        return Command._field_file_name(report.processed_file)

    @staticmethod
    def _content_hash(instance: ImportedMedia) -> str:
        if isinstance(instance, VideoFile):
            return cast(_VideoEvaluationMedia, instance).video_hash
        return cast(_ReportEvaluationMedia, instance).pdf_hash

    @staticmethod
    def _processed_hash(instance: ImportedMedia) -> str:
        if isinstance(instance, VideoFile):
            video = cast(_VideoEvaluationMedia, instance)
            return str(video.processed_video_hash or "")
        return ""

    @staticmethod
    def _summarize(
        results: list[LxAnonymizerPerformanceRunPayload],
    ) -> LxAnonymizerPerformanceSummaryPayload:
        ok_results = [result for result in results if result.ok]
        failed_results = [result for result in results if not result.ok]
        anonymizer_durations = [
            result.anonymizer_seconds
            for result in ok_results
            if result.anonymizer_seconds is not None
        ]
        import_durations = [result.import_seconds for result in ok_results]
        total_durations = [result.total_seconds for result in ok_results]
        return LxAnonymizerPerformanceSummaryPayload(
            total_runs=len(results),
            ok_runs=len(ok_results),
            failed_runs=len(failed_results),
            short_circuited_runs=sum(
                1 for result in ok_results if result.short_circuited
            ),
            total_seconds=sum(total_durations),
            import_seconds=Command._duration_stats(import_durations),
            anonymizer_seconds=Command._duration_stats(anonymizer_durations),
            end_to_end_seconds=Command._duration_stats(total_durations),
        )

    @staticmethod
    def _duration_stats(values: list[float]) -> LxAnonymizerDurationStatsPayload:
        if not values:
            return LxAnonymizerDurationStatsPayload(
                count=0,
                min=0.0,
                mean=0.0,
                max=0.0,
                p95=0.0,
            )
        sorted_values = sorted(values)
        return LxAnonymizerDurationStatsPayload(
            count=len(values),
            min=sorted_values[0],
            mean=sum(sorted_values) / len(sorted_values),
            max=sorted_values[-1],
            p95=Command._percentile(sorted_values, 0.95),
        )

    @staticmethod
    def _percentile(sorted_values: list[float], percentile: float) -> float:
        if not sorted_values:
            return 0.0
        index = min(
            len(sorted_values) - 1,
            max(0, round((len(sorted_values) - 1) * percentile)),
        )
        return sorted_values[index]

    def _write_json(
        self,
        destination: Path,
        payload: LxAnonymizerPerformancePayload,
    ) -> None:
        encoded = payload.model_dump_json(indent=2).encode("utf-8")
        atomic_write_file(
            destination=destination,
            content=[encoded],
            required_bytes=len(encoded),
        )

    def _write_csv(
        self,
        destination: Path,
        results: list[LxAnonymizerPerformanceRunPayload],
    ) -> None:
        buffer = io.StringIO()
        writer = csv.DictWriter(
            buffer,
            fieldnames=list(LX_ANONYMIZER_PERFORMANCE_CSV_FIELDNAMES),
        )
        writer.writeheader()
        for result in results:
            writer.writerow(dump_lx_anonymizer_performance_run_csv_row(result))
        encoded = buffer.getvalue().encode("utf-8")
        atomic_write_file(
            destination=destination,
            content=[encoded],
            required_bytes=len(encoded),
        )

    def _write_text_summary(self, payload: LxAnonymizerPerformancePayload) -> None:
        summary = payload.summary
        self.stdout.write(self.style.SUCCESS("lx_anonymizer evaluation complete"))
        self.stdout.write(
            "runs={total_runs} ok={ok_runs} failed={failed_runs} "
            "short_circuited={short_circuited_runs}".format(
                total_runs=summary.total_runs,
                ok_runs=summary.ok_runs,
                failed_runs=summary.failed_runs,
                short_circuited_runs=summary.short_circuited_runs,
            )
        )
        self.stdout.write(
            "anonymizer_seconds: mean={mean:.3f} p95={p95:.3f} max={max:.3f}".format(
                mean=summary.anonymizer_seconds.mean,
                p95=summary.anonymizer_seconds.p95,
                max=summary.anonymizer_seconds.max,
            )
        )
        self.stdout.write(
            "import_seconds: mean={mean:.3f} p95={p95:.3f} max={max:.3f}".format(
                mean=summary.import_seconds.mean,
                p95=summary.import_seconds.p95,
                max=summary.import_seconds.max,
            )
        )
