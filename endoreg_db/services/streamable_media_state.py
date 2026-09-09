from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from endoreg_db.services.streamable_media_types import (
    STREAMABLE_ARTIFACT_SPECS,
    StreamableArtifactDecision,
    StreamableArtifactDisposition,
    StreamableArtifactKind,
    StreamableArtifactSpec,
    StreamableMediaState,
)
from endoreg_db.utils.storage_profile import (
    PayloadKind,
    StoragePolicy,
    resolve_storage_policy,
)

if TYPE_CHECKING:
    from endoreg_db.models.media.video.video_file import VideoFile

StoragePolicyResolver = Callable[[PayloadKind], StoragePolicy]


class StreamableTargetResolver(Protocol):
    def __call__(self, video: "VideoFile", *, spec: StreamableArtifactSpec) -> Path: ...


def _include_streamable_kind(
    kind: StreamableArtifactKind,
    *,
    include_raw: bool,
    include_processed: bool,
) -> bool:
    match kind:
        case StreamableArtifactKind.RAW:
            return include_raw
        case StreamableArtifactKind.PROCESSED:
            return include_processed


def _resolve_streamable_artifact_decision(
    video: "VideoFile",
    *,
    spec: StreamableArtifactSpec,
    include: bool,
    target_path_for_spec: StreamableTargetResolver,
    resolve_policy: StoragePolicyResolver,
) -> StreamableArtifactDecision:
    storage_policy = resolve_policy(spec.payload_kind)
    file_obj = getattr(video, spec.file_attr, None)
    field_file = file_obj if hasattr(file_obj, "name") else None
    field_file_name_raw = getattr(field_file, "name", None)
    field_file_name = (
        field_file_name_raw if isinstance(field_file_name_raw, str) else ""
    )
    current_relative_path = str(getattr(video, spec.relative_path_attr, "") or "")
    expected_hash = str(getattr(video, spec.hash_attr, "") or "").strip()
    has_named_source = field_file is not None and bool(field_file_name)

    if include and storage_policy == StoragePolicy.FS_STREAMABLE and has_named_source:
        return StreamableArtifactDecision(
            spec=spec,
            include=include,
            storage_policy=storage_policy,
            disposition=StreamableArtifactDisposition.SYNC,
            field_file=field_file,
            field_file_name=field_file_name,
            current_relative_path=current_relative_path,
            expected_hash=expected_hash,
            target_path=target_path_for_spec(video, spec=spec),
        )

    disposition = (
        StreamableArtifactDisposition.CLEAR_STALE_PATH
        if include and has_named_source and current_relative_path
        else StreamableArtifactDisposition.IGNORE
    )
    return StreamableArtifactDecision(
        spec=spec,
        include=include,
        storage_policy=storage_policy,
        disposition=disposition,
        field_file=field_file,
        field_file_name=field_file_name,
        current_relative_path=current_relative_path,
        expected_hash=expected_hash,
        target_path=None,
    )


def build_streamable_media_state(
    video: "VideoFile",
    *,
    include_raw: bool = True,
    include_processed: bool = True,
    target_path_for_spec: StreamableTargetResolver,
    resolve_policy: StoragePolicyResolver = resolve_storage_policy,
) -> StreamableMediaState:
    return StreamableMediaState(
        artifacts=tuple(
            _resolve_streamable_artifact_decision(
                video,
                spec=spec,
                include=_include_streamable_kind(
                    spec.kind,
                    include_raw=include_raw,
                    include_processed=include_processed,
                ),
                target_path_for_spec=target_path_for_spec,
                resolve_policy=resolve_policy,
            )
            for spec in STREAMABLE_ARTIFACT_SPECS
        )
    )
