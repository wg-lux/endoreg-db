from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, cast

from django.core.management.base import BaseCommand, CommandError, CommandParser


class ReliefResourceKind(str, Enum):
    VIDEO = "video"
    REPORT = "report"


@dataclass(frozen=True)
class StorageReliefConfig:
    archive_root: Path
    manifest_dir: Path
    staging_root: Path
    dry_run: bool
    delete_after_verify: bool
    include_legacy_processed_duplicates: bool
    include_validated_export_bundles: bool
    validated_export_dirs: list[Path]
    validated_export_marker_names: list[str]
    legacy_duplicate_sources: list[dict[str, Any]]

    @classmethod
    def load(cls, path: Path) -> "StorageReliefConfig":
        with path.open("r", encoding="utf-8") as handle:
            raw_data = json.load(handle)
        if not isinstance(raw_data, dict):
            raise ValueError("storage relief config must be a JSON object")
        data = cast(dict[str, object], raw_data)
        validated_export_dirs = _object_list(data.get("validated_export_dirs", []))
        validated_export_marker_names = _object_list(
            data.get("validated_export_marker_names", [])
        )
        legacy_duplicate_sources = [
            cast(dict[str, Any], item)
            for item in _object_list(data.get("legacy_duplicate_sources", []))
            if isinstance(item, dict)
        ]
        return cls(
            archive_root=Path(str(data["archive_root"])),
            manifest_dir=Path(str(data["manifest_dir"])),
            staging_root=Path(str(data["staging_dir"])),
            dry_run=bool(data.get("dry_run", False)),
            delete_after_verify=bool(data.get("delete_after_verify", True)),
            include_legacy_processed_duplicates=bool(
                data.get("include_legacy_processed_duplicates", True)
            ),
            include_validated_export_bundles=bool(
                data.get("include_validated_export_bundles", True)
            ),
            validated_export_dirs=[Path(str(value)) for value in validated_export_dirs],
            validated_export_marker_names=[
                str(value) for value in validated_export_marker_names
            ],
            legacy_duplicate_sources=legacy_duplicate_sources,
        )


ResourceRecord = dict[str, Any]
ResourceIndex = dict[str, dict[str, ResourceRecord]]
ArchiveItem = dict[str, Any]


def _object_list(value: object) -> list[object]:
    if not isinstance(value, list):
        return []
    return cast(list[object], value)


def emit(event: str, **payload: object) -> None:
    print(json.dumps({"event": event, **payload}, sort_keys=True), flush=True)


def parse_resource_kind(value: object) -> ReliefResourceKind | None:
    try:
        return ReliefResourceKind(str(value).lower())
    except ValueError:
        return None


def state_is_processed_anonymized(state: object | None) -> bool:
    if state is None:
        return False
    return bool(
        getattr(state, "anonymization_validated", False)
        or getattr(state, "sensitive_meta_processed", False)
        or getattr(state, "anonymized", False)
    )


def state_is_validated(state: object | None) -> bool:
    return bool(state is not None and getattr(state, "anonymization_validated", False))


def field_name_keys(field_name: str) -> set[str]:
    normalized = field_name.strip("/")
    keys = {normalized, Path(normalized).name}
    parts = normalized.split("/", 1)
    if len(parts) == 2:
        keys.add(parts[1])
    return {key for key in keys if key}


def resource_identifier(kind: ReliefResourceKind, obj: object) -> str:
    return f"{kind.value}:{getattr(obj, 'pk', '')}"


def empty_resource_index() -> ResourceIndex:
    return {kind.value: {} for kind in ReliefResourceKind}


def add_resource(index: ResourceIndex, record: ResourceRecord, field_name: str) -> None:
    kind = str(record["kind"])
    for key in field_name_keys(field_name):
        index[kind][key] = record


def build_eligible_resources() -> ResourceIndex:
    from endoreg_db.models.media.pdf.raw_pdf import RawPdfFile
    from endoreg_db.models.media.video.video_file import VideoFile

    resources = empty_resource_index()

    videos = (
        VideoFile.objects.select_related("state")
        .exclude(processed_file="")
        .exclude(processed_file__isnull=True)
        .order_by("pk")
    )
    for video in videos.iterator():
        field = getattr(video, "processed_file", None)
        field_name = str(getattr(field, "name", "") or "")
        state = getattr(video, "state", None)
        if not field_name or not state_is_processed_anonymized(state):
            continue
        add_resource(
            resources,
            {
                "kind": ReliefResourceKind.VIDEO.value,
                "object": video,
                "field_file": field,
                "content_hash": getattr(video, "processed_video_hash", None) or None,
                "validated": state_is_validated(state),
                "identifier": resource_identifier(ReliefResourceKind.VIDEO, video),
            },
            field_name,
        )

    reports = (
        RawPdfFile.objects.select_related("state")
        .exclude(processed_file="")
        .exclude(processed_file__isnull=True)
        .order_by("pk")
    )
    for report in reports.iterator():
        field = getattr(report, "processed_file", None)
        field_name = str(getattr(field, "name", "") or "")
        state = getattr(report, "state", None)
        if not field_name or not state_is_processed_anonymized(state):
            continue
        add_resource(
            resources,
            {
                "kind": ReliefResourceKind.REPORT.value,
                "object": report,
                "field_file": field,
                "content_hash": None,
                "validated": state_is_validated(state),
                "identifier": resource_identifier(ReliefResourceKind.REPORT, report),
            },
            field_name,
        )

    return resources


def get_record_hash(record: ResourceRecord) -> str:
    cached = record.get("content_hash")
    if cached:
        return str(cached)
    from endoreg_db.utils.file_operations import sha256_file

    digest = sha256_file(record["field_file"])
    record["content_hash"] = digest
    return digest


def archive_destination(
    root: Path,
    category: str,
    label: str,
    rel_path: Path,
    content_hash: str,
) -> Path:
    destination = root / category / label / rel_path
    if not destination.exists():
        return destination
    return destination.with_name(f"{destination.name}.{content_hash[:16]}")


def staging_destination(
    staging_root: Path,
    archive_root: Path,
    destination: Path,
    content_hash: str,
) -> Path:
    final_rel_path = destination.resolve().relative_to(archive_root.resolve())
    staged = staging_root / final_rel_path
    return staged.with_name(f"{staged.name}.{os.getpid()}.{content_hash[:16]}.staging")


def ensure_archive_path(path: Path, archive_root: Path) -> None:
    resolved_path = path.resolve()
    resolved_archive = archive_root.resolve()
    if resolved_path == resolved_archive or resolved_archive in resolved_path.parents:
        return
    raise ValueError(f"refusing to write outside archive root: {path}")


def copy_verify_delete(
    *,
    source: Path,
    destination: Path,
    archive_root: Path,
    staging_root: Path,
    dry_run: bool,
    delete_after_verify: bool,
    expected_hash: str | None,
) -> ArchiveItem:
    from endoreg_db.utils.file_operations import (
        atomic_copy_file,
        atomic_move_file,
        safe_unlink_file,
        sha256_file,
    )

    ensure_archive_path(destination, archive_root)
    ensure_archive_path(staging_root, archive_root)
    size_bytes = source.stat().st_size
    source_hash = sha256_file(source)
    if expected_hash is not None and source_hash != expected_hash:
        return {
            "status": "skipped",
            "reason": "source hash does not match eligible database payload",
            "source": str(source),
            "source_hash": source_hash,
            "expected_hash": expected_hash,
        }

    if dry_run:
        return {
            "status": "planned",
            "source": str(source),
            "destination": str(destination),
            "source_hash": source_hash,
            "bytes": size_bytes,
        }

    staged = staging_destination(staging_root, archive_root, destination, source_hash)
    ensure_archive_path(staged, archive_root)
    try:
        atomic_copy_file(
            source=source,
            destination=staged,
            preserve_metadata=True,
            file_mode=0o640,
            dir_mode=0o750,
        )
        staged_hash = sha256_file(staged)
        if staged_hash != source_hash:
            raise RuntimeError(
                f"staging hash verification failed for {source}: "
                f"{staged_hash} != {source_hash}"
            )
        atomic_move_file(
            source=staged,
            destination=destination,
            file_mode=0o640,
            dir_mode=0o750,
        )
    except Exception:
        if staged.exists():
            safe_unlink_file(staged, missing_ok=True)
        raise

    destination_hash = sha256_file(destination)
    if destination_hash != source_hash:
        raise RuntimeError(
            f"archive hash verification failed for {source}: "
            f"{destination_hash} != {source_hash}"
        )

    deleted = False
    if delete_after_verify:
        safe_unlink_file(source, missing_ok=False)
        deleted = True

    return {
        "status": "archived",
        "source": str(source),
        "destination": str(destination),
        "staging": str(staged),
        "source_hash": source_hash,
        "bytes": size_bytes,
        "deleted": deleted,
    }


def find_resource_record(
    *,
    resources: ResourceIndex,
    kind: ReliefResourceKind,
    rel_path: Path,
) -> ResourceRecord | None:
    keys = {rel_path.as_posix(), rel_path.name}
    return next(
        (
            resources.get(kind.value, {}).get(key)
            for key in keys
            if resources.get(kind.value, {}).get(key)
        ),
        None,
    )


def archive_legacy_duplicates(
    *,
    config: StorageReliefConfig,
    resources: ResourceIndex,
) -> list[ArchiveItem]:
    items: list[ArchiveItem] = []
    for source_config in config.legacy_duplicate_sources:
        kind = parse_resource_kind(source_config.get("kind"))
        if kind is None:
            emit(
                "lx_annotate_storage_relief_skip",
                reason="unknown legacy duplicate source kind",
                kind=str(source_config.get("kind")),
            )
            continue

        source_root = Path(str(source_config["source_root"]))
        label = str(source_config["label"]).strip("/")
        if not source_root.is_dir():
            emit(
                "lx_annotate_storage_relief_skip",
                reason="legacy source missing",
                source_root=str(source_root),
                kind=kind.value,
            )
            continue

        for source in sorted(path for path in source_root.rglob("*") if path.is_file()):
            rel_path = source.relative_to(source_root)
            record = find_resource_record(
                resources=resources,
                kind=kind,
                rel_path=rel_path,
            )
            if record is None:
                result: ArchiveItem = {
                    "status": "skipped",
                    "reason": "no eligible database payload",
                    "kind": kind.value,
                    "source": str(source),
                }
                items.append(result)
                continue

            expected_hash = get_record_hash(record)
            destination = archive_destination(
                config.archive_root,
                "duplicates",
                label,
                rel_path,
                expected_hash,
            )
            result = copy_verify_delete(
                source=source,
                destination=destination,
                archive_root=config.archive_root,
                staging_root=config.staging_root,
                dry_run=config.dry_run,
                delete_after_verify=config.delete_after_verify,
                expected_hash=expected_hash,
            )
            result.update(
                {
                    "kind": kind.value,
                    "category": "legacy_processed_duplicate",
                    "resource": record["identifier"],
                    "label": label,
                }
            )
            emit("lx_annotate_storage_relief_item", **result)
            items.append(result)
    return items


def marker_payload(marker: Path) -> dict[str, Any] | None:
    try:
        raw_payload = json.loads(marker.read_text(encoding="utf-8"))
    except Exception as exc:
        emit(
            "lx_annotate_storage_relief_skip",
            reason="invalid export marker json",
            marker=str(marker),
            detail=str(exc),
        )
        return None
    if not isinstance(raw_payload, dict):
        return None
    payload = cast(dict[str, Any], raw_payload)
    if payload.get("validated") is not True:
        return None
    return payload


def marker_resources(payload: dict[str, Any]) -> list[dict[str, Any]]:
    raw_resources = payload.get("resources")
    if raw_resources is None:
        resources = [
            {
                "kind": payload.get("resource_kind"),
                "id": payload.get("resource_id"),
            }
        ]
    else:
        resources = _object_list(raw_resources)
    return [cast(dict[str, Any], item) for item in resources if isinstance(item, dict)]


def resource_is_validated(resource: dict[str, Any]) -> bool:
    from endoreg_db.models.media.pdf.raw_pdf import RawPdfFile
    from endoreg_db.models.media.video.video_file import VideoFile

    kind = parse_resource_kind(resource.get("kind") or resource.get("resource_kind"))
    pk = resource.get("id", resource.get("pk", resource.get("resource_id")))
    if kind is None or pk in {None, ""}:
        return False
    if kind is ReliefResourceKind.VIDEO:
        model = VideoFile
    elif kind is ReliefResourceKind.REPORT:
        model = RawPdfFile
    else:
        raise ValueError(f"unhandled relief resource kind: {kind.value}")
    obj = model.objects.select_related("state").filter(pk=pk).first()
    return bool(obj is not None and state_is_validated(getattr(obj, "state", None)))


def find_validated_bundle_roots(
    export_dir: Path,
    marker_names: list[str],
) -> list[tuple[Path, Path]]:
    bundles: list[tuple[Path, Path]] = []
    if not export_dir.is_dir() or not marker_names:
        return bundles
    for root, dirs, files in os.walk(export_dir):
        root_path = Path(root)
        marker_name = next((name for name in marker_names if name in files), None)
        if marker_name is None:
            continue
        bundles.append((root_path, root_path / marker_name))
        dirs[:] = []
    return bundles


def bundle_resources_are_validated(payload: dict[str, Any]) -> bool:
    resources = marker_resources(payload)
    return bool(resources) and all(
        resource_is_validated(resource) for resource in resources
    )


def validated_bundle_label(bundle_root: Path, export_dir: Path) -> str:
    label = bundle_root.relative_to(export_dir).as_posix()
    return export_dir.name if label == "." else label


def archive_validated_bundle(
    *,
    config: StorageReliefConfig,
    export_dir: Path,
    bundle_root: Path,
    marker: Path,
) -> list[ArchiveItem]:
    items: list[ArchiveItem] = []
    bundle_label = validated_bundle_label(bundle_root, export_dir)
    for source in sorted(path for path in bundle_root.rglob("*") if path.is_file()):
        rel_path = source.relative_to(bundle_root)
        destination = archive_destination(
            config.archive_root,
            "validated-export-bundles",
            bundle_label,
            rel_path,
            datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S"),
        )
        result = copy_verify_delete(
            source=source,
            destination=destination,
            archive_root=config.archive_root,
            staging_root=config.staging_root,
            dry_run=config.dry_run,
            delete_after_verify=config.delete_after_verify,
            expected_hash=None,
        )
        result.update(
            {
                "category": "validated_export_bundle",
                "bundle_root": str(bundle_root),
                "marker": str(marker),
            }
        )
        emit("lx_annotate_storage_relief_item", **result)
        items.append(result)
    return items


def archive_validated_export_bundles(
    *,
    config: StorageReliefConfig,
) -> list[ArchiveItem]:
    items: list[ArchiveItem] = []
    for export_dir in config.validated_export_dirs:
        for bundle_root, marker in find_validated_bundle_roots(
            export_dir,
            config.validated_export_marker_names,
        ):
            payload = marker_payload(marker)
            if payload is None:
                continue
            if not bundle_resources_are_validated(payload):
                emit(
                    "lx_annotate_storage_relief_skip",
                    reason="export bundle resources are not validated",
                    bundle_root=str(bundle_root),
                    marker=str(marker),
                )
                continue
            items.extend(
                archive_validated_bundle(
                    config=config,
                    export_dir=export_dir,
                    bundle_root=bundle_root,
                    marker=marker,
                )
            )
    return items


def manifest_payload(
    *,
    config: StorageReliefConfig,
    items: list[ArchiveItem],
) -> dict[str, Any]:
    return {
        "schema": "lx_annotate_emergency_storage_relief.v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "archive_root": str(config.archive_root),
        "staging_root": str(config.staging_root),
        "dry_run": config.dry_run,
        "items": items,
        "archived_count": count_items(items, "archived"),
        "planned_count": count_items(items, "planned"),
        "skipped_count": count_items(items, "skipped"),
        "freed_bytes": freed_bytes(items),
    }


def write_manifest(config: StorageReliefConfig, items: list[ArchiveItem]) -> Path:
    from endoreg_db.utils.file_operations import atomic_write_file, ensure_directory

    ensure_directory(config.manifest_dir, dir_mode=0o750)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    payload = json.dumps(
        manifest_payload(config=config, items=items),
        indent=2,
        sort_keys=True,
    ).encode("utf-8")
    destination = config.manifest_dir / f"{timestamp}.json"
    atomic_write_file(
        destination=destination,
        content=[payload],
        required_bytes=len(payload),
        file_mode=0o640,
        dir_mode=0o750,
    )
    return destination


def count_items(items: list[ArchiveItem], status: str) -> int:
    return sum(1 for item in items if item.get("status") == status)


def freed_bytes(items: list[ArchiveItem]) -> int:
    return sum(
        int(item.get("bytes", 0)) for item in items if item.get("deleted") is True
    )


def run(config: StorageReliefConfig) -> Path:
    resources = build_eligible_resources()
    items: list[ArchiveItem] = []

    emit(
        "lx_annotate_storage_relief_start",
        archive_root=str(config.archive_root),
        staging_root=str(config.staging_root),
        dry_run=config.dry_run,
        eligible_videos=len(resources["video"]),
        eligible_reports=len(resources["report"]),
    )

    if config.include_legacy_processed_duplicates:
        items.extend(
            archive_legacy_duplicates(
                config=config,
                resources=resources,
            )
        )

    if config.include_validated_export_bundles:
        items.extend(archive_validated_export_bundles(config=config))

    manifest_path = write_manifest(config, items)
    emit(
        "lx_annotate_storage_relief_complete",
        manifest=str(manifest_path),
        archived_count=count_items(items, "archived"),
        planned_count=count_items(items, "planned"),
        skipped_count=count_items(items, "skipped"),
        freed_bytes=freed_bytes(items),
    )
    return manifest_path


class Command(BaseCommand):
    help = (
        "Archive verified lx-annotate duplicates and validated export bundles "
        "to an external storage-relief archive."
    )

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--config", required=True)

    def handle(self, *args: object, **options: object) -> None:
        _ = args
        try:
            config_path = Path(str(options["config"]))
            run(StorageReliefConfig.load(config_path))
        except Exception as exc:
            emit("lx_annotate_storage_relief_error", detail=str(exc))
            raise CommandError(str(exc)) from exc
