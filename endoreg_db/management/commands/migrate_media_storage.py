from __future__ import annotations

import errno
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Literal, Protocol, TypedDict, Unpack, cast

from django.core.management.base import BaseCommand, CommandError, CommandParser
from django.db import models
from django.db.utils import OperationalError, ProgrammingError
from lx_dtypes.models.contracts.json_types import JsonObject
from lx_dtypes.models.contracts.management_command import (
    MigrateMediaStorageCommandOptionsPayload,
)

from endoreg_db.import_files.file_storage.cleanup import (
    is_safe_staging_path,
    safe_cleanup_staging_file,
)
from endoreg_db.models.media.pdf.raw_pdf import RawPdfFile
from endoreg_db.models.media.video.video_file import VideoFile
from endoreg_db.models.state.audit_ledger import AuditLedger
from endoreg_db.services.streamable_media import sync_video_streamable_artifacts
from endoreg_db.utils.encryption.encrypted import MAGIC as LX_ENCRYPTED_MAGIC
from endoreg_db.utils.file_operations import sha256_file
from endoreg_db.utils.paths import (
    EndoregPathsModel,
    protected_media_root,
    resolve_existing_protected_media_path,
)
from endoreg_db.utils.storage import (
    field_file_is_readable,
    save_local_file,
)

logger = logging.getLogger(__name__)

ObjectKind = Literal["video", "report"]
SourceKind = Literal["legacy_path", "streamable_path"]

ACTIONABLE_STATUSES = {
    "would_migrate",
    "would_repair",
    "would_sync_streamable",
}
REPORTABLE_STATUSES = ACTIONABLE_STATUSES | {"failed"}


class _StorageBackedFile(Protocol):
    name: str
    storage: Any


class _AuditLedgerDataRecord(Protocol):
    data: JsonObject


class MigrateMediaStorageCommandOptions(TypedDict):
    apply: bool
    limit: int | None
    repeat_until_empty: bool
    json: bool
    fail_fast: bool
    include_raw: bool
    include_processed: bool
    include_reports: bool
    include_streamable: bool
    delete_verified_legacy: bool
    video_ids: list[int] | None
    hash_value: str | None


@dataclass(frozen=True)
class MediaFieldSpec:
    object_kind: ObjectKind
    field_name: str
    hash_attr: str
    default_suffix: str
    legacy_root_attrs: tuple[str, ...]
    lookup_hash_attrs: tuple[str, ...] = ()
    streamable_attr: str = ""


@dataclass(frozen=True)
class SourceCandidate:
    path: Path
    kind: SourceKind
    label: str


@dataclass(frozen=True)
class FieldPlan:
    object_kind: ObjectKind
    object_pk: int | str
    field_name: str
    status: str
    reason: str = ""
    source: SourceCandidate | None = None
    target_name: str = ""
    cleanup_eligible: bool = False

    @property
    def actionable(self) -> bool:
        return self.status in ACTIONABLE_STATUSES

    @property
    def reportable(self) -> bool:
        return self.status in REPORTABLE_STATUSES


@dataclass(frozen=True)
class RecordPlan:
    object_kind: ObjectKind
    object_pk: int
    field_plans: tuple[FieldPlan, ...]

    @property
    def actionable(self) -> bool:
        return any(plan.actionable for plan in self.field_plans)

    @property
    def reportable(self) -> bool:
        return any(plan.reportable for plan in self.field_plans)


def _emit_event(**payload: object) -> None:
    logger.info(
        "%s",
        json.dumps(
            {"event": "media_storage_migration", **payload},
            sort_keys=True,
            default=str,
        ),
    )


def _is_sha256_hex(value: str | None) -> bool:
    if not value:
        return False
    value = value.strip()
    return len(value) == 64 and all(char in "0123456789abcdefABCDEF" for char in value)


def _path_starts_with_magic(path: Path) -> bool:
    with path.open("rb") as handle:
        return handle.read(len(LX_ENCRYPTED_MAGIC)) == LX_ENCRYPTED_MAGIC


def _as_storage_backed_file(field_file: object) -> _StorageBackedFile | None:
    name = getattr(field_file, "name", None)
    if not isinstance(name, str) or not name:
        return None
    if getattr(field_file, "storage", None) is None:
        return None
    return cast(_StorageBackedFile, field_file)


def _field_file_has_name(field_file: object) -> bool:
    return _as_storage_backed_file(field_file) is not None


def _has_date_modified(instance: models.Model) -> bool:
    return any(field.name == "date_modified" for field in instance._meta.fields)


def _update_fields(instance: models.Model, *field_names: str) -> list[str]:
    unique = list(dict.fromkeys(name for name in field_names if name))
    if _has_date_modified(instance) and "date_modified" not in unique:
        unique.append("date_modified")
    return unique


def _safe_field_storage_path(field_file: object) -> Path | None:
    named_file = _as_storage_backed_file(field_file)
    if named_file is None:
        return None
    try:
        return Path(named_file.storage.path(named_file.name)).resolve()
    except (AttributeError, NotImplementedError, OSError, ValueError):
        return None


def _field_storage_exists(field_file: object) -> bool:
    named_file = _as_storage_backed_file(field_file)
    if named_file is None:
        return False
    try:
        return bool(named_file.storage.exists(named_file.name))
    except Exception:
        return False


def _field_is_repairable_plaintext(field_file: object) -> bool:
    named_file = _as_storage_backed_file(field_file)
    if named_file is None:
        return False
    storage = named_file.storage
    if storage is None or not hasattr(storage, "is_encrypted"):
        return False
    try:
        return bool(storage.exists(named_file.name)) and not storage.is_encrypted(
            named_file.name
        )
    except Exception:
        return False


def _repair_plaintext_field_file(field_file: object) -> bool:
    named_file = _as_storage_backed_file(field_file)
    if named_file is None:
        raise RuntimeError("FieldFile has no storage name")
    storage = named_file.storage
    if storage is None or not hasattr(storage, "repair_plaintext_file"):
        raise RuntimeError("FieldFile storage does not support plaintext repair")
    return bool(storage.repair_plaintext_file(named_file.name))


def _field_is_encrypted_at_rest(field_file: object) -> bool:
    named_file = _as_storage_backed_file(field_file)
    if named_file is None:
        return False
    storage = named_file.storage
    if storage is None or not hasattr(storage, "is_encrypted"):
        return False
    try:
        return bool(storage.is_encrypted(named_file.name))
    except Exception:
        return False


def _append_audit_once(
    *,
    instance: models.Model,
    action: str,
    data: JsonObject,
) -> None:
    object_type = instance.__class__.__name__
    object_pk = str(instance.pk)
    try:
        existing = AuditLedger.objects.filter(
            object_type=object_type,
            object_pk=object_pk,
            action=action,
        )
        for record in cast(Iterable[_AuditLedgerDataRecord], existing.iterator()):
            if record.data == data:
                return
        AuditLedger.objects.create(
            object_type=object_type,
            object_pk=object_pk,
            action=action,
            data=data,
        )
    except (OperationalError, ProgrammingError) as exc:
        logger.warning("AuditLedger unavailable for media migration event: %s", exc)


class Command(BaseCommand):
    help = (
        "Idempotently align existing VideoFile and RawPdfFile media with "
        "encrypted FileField storage and optional streamable artifacts. Dry-run "
        "is the default; pass --apply to write."
    )

    video_raw_spec = MediaFieldSpec(
        object_kind="video",
        field_name="raw_file",
        hash_attr="video_hash",
        default_suffix=".mp4",
        legacy_root_attrs=(
            "sensitive_video",
            "import_video",
            "watcher_video_drop",
            "upload_api",
            "upload_watcher",
        ),
        streamable_attr="raw_streamable_relative_path",
    )
    video_processed_spec = MediaFieldSpec(
        object_kind="video",
        field_name="processed_file",
        hash_attr="processed_video_hash",
        default_suffix=".mp4",
        legacy_root_attrs=(
            "anonym_video",
            "import_anonymized_video",
            "import_preanonymized",
            "upload_preanonymized",
        ),
        lookup_hash_attrs=("processed_video_hash", "video_hash"),
        streamable_attr="processed_streamable_relative_path",
    )
    report_raw_spec = MediaFieldSpec(
        object_kind="report",
        field_name="file",
        hash_attr="pdf_hash",
        default_suffix=".pdf",
        legacy_root_attrs=("sensitive_report", "import_report", "watcher_report_drop"),
    )
    report_processed_spec = MediaFieldSpec(
        object_kind="report",
        field_name="processed_file",
        hash_attr="",
        default_suffix=".pdf",
        legacy_root_attrs=("anonym_report", "import_anonymized_report"),
        lookup_hash_attrs=("pdf_hash",),
    )

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Apply storage changes. Without this flag the command is dry-run.",
        )
        parser.add_argument("--limit", type=int, help="Maximum actionable records.")
        parser.add_argument(
            "--repeat-until-empty",
            action="store_true",
            help="Repeat apply batches until no more actionable records are found.",
        )
        parser.add_argument(
            "--json",
            action="store_true",
            help="Emit a stable JSON summary to stdout.",
        )
        parser.add_argument(
            "--fail-fast",
            action="store_true",
            help="Abort on the first per-record failure.",
        )
        parser.add_argument("--include-raw", action="store_true")
        parser.add_argument("--include-processed", action="store_true")
        parser.add_argument("--include-reports", action="store_true")
        parser.add_argument("--include-streamable", action="store_true")
        parser.add_argument(
            "--delete-verified-legacy",
            action="store_true",
            help=(
                "Delete eligible legacy plaintext sources only after the encrypted "
                "FieldFile copy is verified readable."
            ),
        )
        parser.add_argument(
            "--video-id",
            type=int,
            action="append",
            dest="video_ids",
            help="Restrict to one or more VideoFile primary keys.",
        )
        parser.add_argument(
            "--hash",
            dest="hash_value",
            help="Restrict to matching video_hash, processed_video_hash, or pdf_hash.",
        )

    def handle(
        self,
        *args: str,
        **options: Unpack[MigrateMediaStorageCommandOptions],
    ) -> None:
        options_payload = MigrateMediaStorageCommandOptionsPayload.model_validate(
            options
        )
        apply = options_payload.apply
        repeat = options_payload.repeat_until_empty
        if repeat and not apply:
            raise CommandError("--repeat-until-empty requires --apply")

        limit = options_payload.limit
        if limit is not None and limit <= 0:
            raise CommandError("--limit must be a positive integer")

        includes = self._resolve_includes(options_payload)
        summary = self._empty_summary(
            apply=apply,
            limit=limit,
            repeat_until_empty=repeat,
            includes=includes,
            delete_verified_legacy=options_payload.delete_verified_legacy,
        )

        while True:
            iteration_summary = self._run_iteration(options_payload, includes)
            summary["iterations"] += 1
            self._merge_summary(summary, iteration_summary)

            if not repeat:
                break
            if iteration_summary["selected"] == 0:
                break
            if iteration_summary["changed"] == 0:
                break

        if options_payload.json_output:
            self.stdout.write(json.dumps(summary, sort_keys=True, default=str))
        else:
            self.stdout.write(
                self.style.SUCCESS(
                    "media storage migration complete: "
                    f"apply={apply} scanned={summary['scanned']} "
                    f"selected={summary['selected']} changed={summary['changed']} "
                    f"failed={summary['failed']}"
                )
            )

    def _resolve_includes(
        self, options: MigrateMediaStorageCommandOptionsPayload
    ) -> dict[str, bool]:
        any_scope_flag = any(
            (
                options.include_raw,
                options.include_processed,
                options.include_reports,
                options.include_streamable,
            )
        )
        if not any_scope_flag:
            return {
                "raw": True,
                "processed": True,
                "reports": True,
                "streamable": True,
            }
        return {
            "raw": options.include_raw,
            "processed": options.include_processed,
            "reports": options.include_reports,
            "streamable": options.include_streamable,
        }

    def _empty_summary(
        self,
        *,
        apply: bool,
        limit: int | None,
        repeat_until_empty: bool,
        includes: dict[str, bool],
        delete_verified_legacy: bool,
    ) -> dict[str, Any]:
        return {
            "apply": apply,
            "changed": 0,
            "cleanup_deleted": 0,
            "delete_verified_legacy": delete_verified_legacy,
            "dry_run": not apply,
            "failed": 0,
            "includes": includes,
            "iterations": 0,
            "limit": limit,
            "migrated": 0,
            "records": [],
            "repaired": 0,
            "repeat_until_empty": repeat_until_empty,
            "scanned": 0,
            "selected": 0,
            "streamable_synced": 0,
            "unchanged": 0,
            "would_delete_legacy": 0,
            "would_migrate": 0,
            "would_repair": 0,
            "would_sync_streamable": 0,
        }

    def _merge_summary(
        self, summary: dict[str, Any], iteration: dict[str, Any]
    ) -> None:
        for key in (
            "changed",
            "cleanup_deleted",
            "failed",
            "migrated",
            "repaired",
            "scanned",
            "selected",
            "streamable_synced",
            "unchanged",
            "would_delete_legacy",
            "would_migrate",
            "would_repair",
            "would_sync_streamable",
        ):
            summary[key] += int(iteration.get(key, 0))
        summary["records"].extend(iteration["records"])

    def _run_iteration(
        self,
        options: MigrateMediaStorageCommandOptionsPayload,
        includes: dict[str, bool],
    ) -> dict[str, Any]:
        apply = options.apply
        fail_fast = options.fail_fast
        limit = options.limit
        self._delete_verified_legacy = options.delete_verified_legacy

        reportable_plans = self._collect_reportable_plans(options, includes)
        actionable_plans = [plan for plan in reportable_plans if plan.actionable]
        selected_plans = actionable_plans[:limit] if limit else actionable_plans
        selected_keys = {(plan.object_kind, plan.object_pk) for plan in selected_plans}
        failure_only_plans = [
            plan
            for plan in reportable_plans
            if not plan.actionable
            and (plan.object_kind, plan.object_pk) not in selected_keys
        ]
        iteration: dict[str, Any] = {
            "changed": 0,
            "cleanup_deleted": 0,
            "failed": 0,
            "migrated": 0,
            "records": [],
            "repaired": 0,
            "scanned": getattr(self, "_last_scan_count", 0),
            "selected": len(selected_plans),
            "streamable_synced": 0,
            "unchanged": 0,
            "would_delete_legacy": 0,
            "would_migrate": 0,
            "would_repair": 0,
            "would_sync_streamable": 0,
        }

        for record_plan in selected_plans:
            record_results = self._apply_record_plan(
                record_plan,
                apply=apply,
                includes=includes,
                delete_verified_legacy=options.delete_verified_legacy,
                fail_fast=fail_fast,
            )
            for result in record_results:
                self._count_result(iteration, result)
                iteration["records"].append(result)
                if fail_fast and result["status"] == "failed":
                    return iteration

        for record_plan in failure_only_plans:
            for field_plan in record_plan.field_plans:
                if field_plan.status != "failed":
                    continue
                result = self._result_from_plan(field_plan)
                self._count_result(iteration, result)
                iteration["records"].append(result)
                if fail_fast:
                    return iteration

        if not selected_plans:
            iteration["unchanged"] = max(
                iteration["scanned"] - len(failure_only_plans), 0
            )
        return iteration

    def _collect_reportable_plans(
        self,
        options: MigrateMediaStorageCommandOptionsPayload,
        includes: dict[str, bool],
    ) -> list[RecordPlan]:
        self._last_scan_count = 0
        plans: list[RecordPlan] = []
        for object_kind, instance in self._iter_records(options, includes):
            self._last_scan_count += 1
            record_plan = self._plan_record(
                object_kind,
                instance,
                includes=includes,
            )
            if record_plan.reportable:
                plans.append(record_plan)
        return plans

    def _iter_records(
        self,
        options: MigrateMediaStorageCommandOptionsPayload,
        includes: dict[str, bool],
    ) -> Iterable[tuple[ObjectKind, models.Model]]:
        video_ids = options.video_ids
        hash_value = options.hash_value.strip()

        if includes["raw"] or includes["processed"] or includes["streamable"]:
            video_qs = VideoFile.objects.all().order_by("pk")
            if video_ids:
                video_qs = video_qs.filter(pk__in=video_ids)
            if hash_value:
                video_qs = video_qs.filter(
                    models.Q(video_hash=hash_value)
                    | models.Q(processed_video_hash=hash_value)
                )
            for video in video_qs.iterator():
                yield "video", video

        if includes["reports"] and not video_ids:
            report_qs = RawPdfFile.objects.all().order_by("pk")
            if hash_value:
                report_qs = report_qs.filter(pdf_hash=hash_value)
            for report in report_qs.iterator():
                yield "report", report

    def _plan_record(
        self,
        object_kind: ObjectKind,
        instance: models.Model,
        *,
        includes: dict[str, bool],
    ) -> RecordPlan:
        field_plans: list[FieldPlan] = []
        if object_kind == "video":
            video = cast(VideoFile, instance)
            if includes["raw"]:
                field_plans.append(self._plan_field(video, self.video_raw_spec))
            if includes["processed"]:
                field_plans.append(self._plan_field(video, self.video_processed_spec))
            if includes["streamable"]:
                field_plans.append(
                    self._plan_streamable_video(
                        video,
                        include_raw=includes["raw"],
                        include_processed=includes["processed"],
                    )
                )
        elif includes["reports"]:
            field_plans.append(self._plan_field(instance, self.report_raw_spec))
            field_plans.append(self._plan_field(instance, self.report_processed_spec))

        return RecordPlan(
            object_kind=object_kind,
            object_pk=int(instance.pk),
            field_plans=tuple(field_plans),
        )

    def _plan_field(self, instance: models.Model, spec: MediaFieldSpec) -> FieldPlan:
        field_file = getattr(instance, spec.field_name)
        if _field_file_has_name(field_file) and _field_is_repairable_plaintext(
            field_file
        ):
            return FieldPlan(
                spec.object_kind,
                instance.pk,
                spec.field_name,
                "would_repair",
                reason="plaintext_fieldfile",
                target_name=field_file.name,
            )

        if _field_file_has_name(field_file) and field_file_is_readable(field_file):
            if not _field_is_encrypted_at_rest(field_file):
                return FieldPlan(
                    spec.object_kind,
                    instance.pk,
                    spec.field_name,
                    "failed",
                    reason="validation_failed",
                )
            return FieldPlan(spec.object_kind, instance.pk, spec.field_name, "ok")

        source, rejected_reason = self._find_plaintext_source(instance, spec)
        if source is None:
            if _field_file_has_name(field_file):
                reason = (
                    "missing_source"
                    if not _field_storage_exists(field_file)
                    else rejected_reason or "unreadable_fieldfile"
                )
                return FieldPlan(
                    spec.object_kind,
                    instance.pk,
                    spec.field_name,
                    "failed",
                    reason=reason,
                )
            return FieldPlan(spec.object_kind, instance.pk, spec.field_name, "ok")

        validation_error = self._validate_source(instance, spec, source)
        if validation_error:
            return FieldPlan(
                spec.object_kind,
                instance.pk,
                spec.field_name,
                "failed",
                reason=validation_error,
                source=source,
            )

        cleanup_eligible = source.kind == "legacy_path" and is_safe_staging_path(
            source.path
        )
        return FieldPlan(
            spec.object_kind,
            instance.pk,
            spec.field_name,
            "would_migrate",
            source=source,
            target_name=self._target_filename(instance, spec, source.path),
            cleanup_eligible=cleanup_eligible,
        )

    def _plan_streamable_video(
        self,
        video: VideoFile,
        *,
        include_raw: bool,
        include_processed: bool,
    ) -> FieldPlan:
        try:
            update_fields = sync_video_streamable_artifacts(
                video,
                include_raw=include_raw,
                include_processed=include_processed,
                save=False,
            )
        except Exception as exc:
            logger.exception(
                "Failed to plan streamable media migration for VideoFile %s",
                video.pk,
            )
            return FieldPlan(
                "video",
                video.pk,
                "streamable",
                "failed",
                reason=self._classify_exception(exc),
            )
        if update_fields:
            return FieldPlan(
                "video",
                video.pk,
                "streamable",
                "would_sync_streamable",
                reason=",".join(update_fields),
            )
        return FieldPlan("video", video.pk, "streamable", "ok")

    def _find_plaintext_source(
        self, instance: models.Model, spec: MediaFieldSpec
    ) -> tuple[SourceCandidate | None, str]:
        rejected_reason = ""
        for candidate in self._source_candidates(instance, spec):
            path = candidate.path
            if not path.exists():
                continue
            if not path.is_file() or path.is_symlink():
                rejected_reason = "permission_error"
                continue
            if path.stat().st_size <= 0:
                rejected_reason = "validation_failed"
                continue
            try:
                starts_with_magic = _path_starts_with_magic(path)
            except PermissionError:
                rejected_reason = "permission_error"
                continue
            except OSError:
                rejected_reason = "validation_failed"
                continue
            if starts_with_magic:
                rejected_reason = (
                    "encrypted_blob_in_streamable_path"
                    if candidate.kind == "streamable_path"
                    else "validation_failed"
                )
                continue
            if not self._is_allowed_source_path(path):
                rejected_reason = "permission_error"
                continue
            return candidate, ""
        return None, rejected_reason

    def _source_candidates(
        self, instance: models.Model, spec: MediaFieldSpec
    ) -> Iterable[SourceCandidate]:
        field_file = getattr(instance, spec.field_name)
        seen: set[Path] = set()

        def yield_once(
            path: Path | None, *, kind: SourceKind, label: str
        ) -> Iterable[SourceCandidate]:
            if path is None:
                return
            resolved = path.resolve()
            if resolved in seen:
                return
            seen.add(resolved)
            yield SourceCandidate(resolved, kind, label)

        field_name = getattr(field_file, "name", None)
        if field_name:
            absolute_candidate = Path(field_name)
            if absolute_candidate.is_absolute():
                yield from yield_once(
                    absolute_candidate,
                    kind="legacy_path",
                    label="field_name_absolute",
                )
            yield from yield_once(
                _safe_field_storage_path(field_file),
                kind="legacy_path",
                label="field_storage_path",
            )
            yield from yield_once(
                resolve_existing_protected_media_path(field_name),
                kind="legacy_path",
                label="field_name_relative",
            )

        if spec.streamable_attr:
            streamable_value = getattr(instance, spec.streamable_attr, "")
            yield from yield_once(
                resolve_existing_protected_media_path(streamable_value),
                kind="streamable_path",
                label=spec.streamable_attr,
            )

        stems = self._candidate_stems(instance, spec)
        if not stems:
            return

        paths = EndoregPathsModel.from_environment()
        for root_attr in spec.legacy_root_attrs:
            root = getattr(paths, root_attr)
            for stem in stems:
                for suffix in self._candidate_suffixes(instance, spec):
                    yield from yield_once(
                        root / f"{stem}{suffix}",
                        kind="legacy_path",
                        label=f"{root_attr}/{stem}{suffix}",
                    )

    def _candidate_stems(
        self, instance: models.Model, spec: MediaFieldSpec
    ) -> tuple[str, ...]:
        stems: list[str] = []
        for attr in (spec.hash_attr, *spec.lookup_hash_attrs):
            if not attr:
                continue
            value = getattr(instance, attr, "") or ""
            if value and str(value) not in stems:
                stems.append(str(value))

        if spec.object_kind == "video" and spec.field_name == "processed_file":
            video_hash = getattr(instance, "video_hash", "") or ""
            if video_hash:
                for stem in (
                    f"{video_hash}_processed",
                    f"{video_hash}-processed",
                    f"processed_{video_hash}",
                ):
                    if stem not in stems:
                        stems.append(stem)
        return tuple(stems)

    def _candidate_suffixes(
        self, instance: models.Model, spec: MediaFieldSpec
    ) -> tuple[str, ...]:
        suffixes = [spec.default_suffix]
        if spec.object_kind == "video":
            suffix = getattr(instance, "suffix", "") or ""
            if suffix and suffix not in suffixes:
                suffixes.append(suffix)
        return tuple(suffixes)

    def _is_allowed_source_path(self, path: Path) -> bool:
        if is_safe_staging_path(path):
            return True
        resolved = path.resolve()
        paths = EndoregPathsModel.from_environment()
        for root in (paths.storage, paths.protected_root, protected_media_root()):
            try:
                resolved.relative_to(Path(root).resolve())
                return True
            except ValueError:
                continue
        return False

    def _expected_hash(self, instance: models.Model, spec: MediaFieldSpec) -> str:
        if not spec.hash_attr:
            return ""
        return getattr(instance, spec.hash_attr, "") or ""

    def _should_validate_source_hash(self, spec: MediaFieldSpec) -> bool:
        return bool(spec.hash_attr)

    def _validate_source(
        self, instance: models.Model, spec: MediaFieldSpec, source: SourceCandidate
    ) -> str:
        try:
            if not source.path.exists() or not source.path.is_file():
                return "missing_source"
            if source.path.stat().st_size <= 0:
                return "validation_failed"
            expected_hash = self._expected_hash(instance, spec)
            if (
                self._should_validate_source_hash(spec)
                and _is_sha256_hex(expected_hash)
                and sha256_file(source.path) != expected_hash
            ):
                return "validation_failed"
        except PermissionError:
            return "permission_error"
        except OSError as exc:
            if exc.errno == errno.ENOSPC:
                return "insufficient_storage"
            return "validation_failed"
        return ""

    def _target_filename(
        self, instance: models.Model, spec: MediaFieldSpec, source_path: Path
    ) -> str:
        expected_hash = self._expected_hash(instance, spec)
        suffix = source_path.suffix or spec.default_suffix
        if expected_hash:
            return f"{expected_hash}{suffix}"
        return source_path.name

    def _apply_record_plan(
        self,
        record_plan: RecordPlan,
        *,
        apply: bool,
        includes: dict[str, bool],
        delete_verified_legacy: bool,
        fail_fast: bool,
    ) -> list[dict[str, Any]]:
        instance = self._get_instance(record_plan.object_kind, record_plan.object_pk)
        if instance is None:
            return [
                self._result_from_plan(
                    FieldPlan(
                        record_plan.object_kind,
                        record_plan.object_pk,
                        "record",
                        "failed",
                        reason="missing_source",
                    )
                )
            ]

        results: list[dict[str, Any]] = []
        specs = self._specs_for_record(record_plan.object_kind, includes)
        for spec in specs:
            result = self._execute_field(instance, spec, apply=apply)
            results.append(result)
            if fail_fast and result["status"] == "failed":
                return results

        if record_plan.object_kind == "video" and includes["streamable"]:
            video = cast(VideoFile, instance)
            result = self._execute_streamable(
                video,
                include_raw=includes["raw"],
                include_processed=includes["processed"],
                apply=apply,
            )
            results.append(result)
        return results

    def _get_instance(
        self, object_kind: ObjectKind, pk: int
    ) -> VideoFile | RawPdfFile | None:
        model = VideoFile if object_kind == "video" else RawPdfFile
        try:
            return model.objects.get(pk=pk)
        except model.DoesNotExist:
            return None

    def _specs_for_record(
        self, object_kind: ObjectKind, includes: dict[str, bool]
    ) -> tuple[MediaFieldSpec, ...]:
        if object_kind == "video":
            specs: list[MediaFieldSpec] = []
            if includes["raw"]:
                specs.append(self.video_raw_spec)
            if includes["processed"]:
                specs.append(self.video_processed_spec)
            return tuple(specs)
        if not includes["reports"]:
            return ()
        return (self.report_raw_spec, self.report_processed_spec)

    def _execute_field(
        self, instance: models.Model, spec: MediaFieldSpec, *, apply: bool
    ) -> dict[str, Any]:
        plan = self._plan_field(instance, spec)
        if not plan.actionable:
            return self._result_from_plan(plan)
        if plan.status == "failed":
            result = self._result_from_plan(plan)
            if apply:
                _append_audit_once(
                    instance=instance,
                    action="media_storage_failed",
                    data={
                        "field": spec.field_name,
                        "reason": plan.reason,
                    },
                )
            return result
        if not apply:
            return self._result_from_plan(plan)

        try:
            if plan.status == "would_repair":
                return self._repair_field_file(instance, spec, plan)
            if plan.status == "would_migrate":
                return self._migrate_field_file(instance, spec, plan)
        except Exception as exc:
            reason = self._classify_exception(exc)
            failed_plan = FieldPlan(
                spec.object_kind,
                instance.pk,
                spec.field_name,
                "failed",
                reason=reason,
                source=plan.source,
                target_name=plan.target_name,
            )
            _append_audit_once(
                instance=instance,
                action="media_storage_failed",
                data={"field": spec.field_name, "reason": reason},
            )
            return self._result_from_plan(failed_plan)
        return self._result_from_plan(plan)

    def _repair_field_file(
        self, instance: models.Model, spec: MediaFieldSpec, plan: FieldPlan
    ) -> dict[str, Any]:
        field_file = getattr(instance, spec.field_name)
        _repair_plaintext_field_file(field_file)
        if not field_file_is_readable(field_file):
            raise RuntimeError("validation_failed")
        if not _field_is_encrypted_at_rest(field_file):
            raise RuntimeError("validation_failed")
        _append_audit_once(
            instance=instance,
            action="media_storage_migrated",
            data={
                "field": spec.field_name,
                "operation": "repair_plaintext_fieldfile",
                "stored_name": field_file.name,
            },
        )
        result = self._result_from_plan(
            FieldPlan(
                spec.object_kind,
                instance.pk,
                spec.field_name,
                "repaired",
                reason=plan.reason,
                target_name=field_file.name,
            )
        )
        _emit_event(**result)
        return result

    def _migrate_field_file(
        self, instance: models.Model, spec: MediaFieldSpec, plan: FieldPlan
    ) -> dict[str, Any]:
        if plan.source is None:
            raise RuntimeError("missing_source")
        field_file = getattr(instance, spec.field_name)
        saved_name = save_local_file(
            field_file,
            plan.source.path,
            name=plan.target_name,
            save=False,
        )
        if not field_file_is_readable(field_file):
            raise RuntimeError("validation_failed")
        if not _field_is_encrypted_at_rest(field_file):
            raise RuntimeError("validation_failed")

        instance.save(update_fields=_update_fields(instance, spec.field_name))
        deleted_legacy = False
        cleanup_eligible = plan.source.kind == "legacy_path" and is_safe_staging_path(
            plan.source.path
        )
        if cleanup_eligible and self._delete_verified_legacy_active:
            deleted_legacy = safe_cleanup_staging_file(
                plan.source.path,
                label=f"migrated {spec.object_kind}.{spec.field_name}",
            )

        _append_audit_once(
            instance=instance,
            action="media_storage_migrated",
            data={
                "field": spec.field_name,
                "operation": "save_local_file",
                "source_kind": plan.source.kind,
                "stored_name": saved_name,
            },
        )
        result = self._result_from_plan(
            FieldPlan(
                spec.object_kind,
                instance.pk,
                spec.field_name,
                "migrated",
                source=plan.source,
                target_name=saved_name,
                cleanup_eligible=cleanup_eligible,
            )
        )
        result["cleanup_deleted"] = deleted_legacy
        _emit_event(**result)
        return result

    def _execute_streamable(
        self,
        video: VideoFile,
        *,
        include_raw: bool,
        include_processed: bool,
        apply: bool,
    ) -> dict[str, Any]:
        plan = self._plan_streamable_video(
            video, include_raw=include_raw, include_processed=include_processed
        )
        if not plan.actionable:
            return self._result_from_plan(plan)
        if plan.status == "failed":
            return self._result_from_plan(plan)
        if not apply:
            return self._result_from_plan(plan)
        try:
            update_fields = sync_video_streamable_artifacts(
                video,
                include_raw=include_raw,
                include_processed=include_processed,
                save=True,
            )
            status = "streamable_synced" if update_fields else "ok"
            result_plan = FieldPlan(
                "video",
                video.pk,
                "streamable",
                status,
                reason=",".join(update_fields),
            )
            result = self._result_from_plan(result_plan)
            _emit_event(**result)
            return result
        except Exception as exc:
            return self._result_from_plan(
                FieldPlan(
                    "video",
                    video.pk,
                    "streamable",
                    "failed",
                    reason=self._classify_exception(exc),
                )
            )

    @property
    def _delete_verified_legacy_active(self) -> bool:
        return bool(getattr(self, "_delete_verified_legacy", False))

    def _result_from_plan(self, plan: FieldPlan) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "field": plan.field_name,
            "object_kind": plan.object_kind,
            "object_pk": str(plan.object_pk),
            "status": plan.status,
        }
        if plan.reason:
            payload["reason"] = plan.reason
        if plan.source is not None:
            payload["source_kind"] = plan.source.kind
            payload["source_path"] = str(plan.source.path)
        if plan.target_name:
            payload["target_name"] = plan.target_name
        if plan.cleanup_eligible:
            payload["cleanup_eligible"] = True
        return payload

    def _count_result(self, summary: dict[str, Any], result: dict[str, Any]) -> None:
        status = result["status"]
        if status == "failed":
            summary["failed"] += 1
        elif status == "migrated":
            summary["migrated"] += 1
            summary["changed"] += 1
        elif status == "repaired":
            summary["repaired"] += 1
            summary["changed"] += 1
        elif status == "streamable_synced":
            summary["streamable_synced"] += 1
            summary["changed"] += 1
        elif status == "would_migrate":
            summary["would_migrate"] += 1
            if result.get("cleanup_eligible"):
                summary["would_delete_legacy"] += 1
        elif status == "would_repair":
            summary["would_repair"] += 1
        elif status == "would_sync_streamable":
            summary["would_sync_streamable"] += 1
        elif status == "ok":
            summary["unchanged"] += 1
        if result.get("cleanup_deleted"):
            summary["cleanup_deleted"] += 1

    def _classify_exception(self, exc: Exception) -> str:
        if isinstance(exc, PermissionError):
            return "permission_error"
        if isinstance(exc, OSError) and exc.errno == errno.ENOSPC:
            return "insufficient_storage"
        message = str(exc).lower()
        for reason in (
            "missing_source",
            "unreadable_fieldfile",
            "encrypted_blob_in_streamable_path",
            "insufficient_storage",
            "validation_failed",
            "permission_error",
        ):
            if reason in message:
                return reason
        return "unexpected_error"
