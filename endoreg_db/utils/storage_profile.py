from __future__ import annotations

import logging
from enum import StrEnum

from endoreg_db.config.env import get_endoreg_storage_profile_name

logger = logging.getLogger(__name__)

_STATE = {"legacy_profile_warning_emitted": False}


class StorageProfile(StrEnum):
    STRICT_APP_ENCRYPTED = "strict_app_encrypted"
    FS_ENCRYPTED_STREAMING = "fs_encrypted_streaming"
    HYBRID_DEFAULT = "hybrid_default"


class PayloadKind(StrEnum):
    VIDEO_RAW = "video_raw"
    VIDEO_PROCESSED = "video_processed"
    REPORT_PDF = "report_pdf"
    METADATA = "metadata"
    SIDECAR = "sidecar"
    MANIFEST = "manifest"


class StoragePolicy(StrEnum):
    APP_ENCRYPTED = "app_encrypted"
    FS_STREAMABLE = "fs_streamable"


PROFILE_POLICY_MAP: dict[StorageProfile, dict[PayloadKind, StoragePolicy]] = {
    StorageProfile.STRICT_APP_ENCRYPTED: {
        PayloadKind.VIDEO_RAW: StoragePolicy.APP_ENCRYPTED,
        PayloadKind.VIDEO_PROCESSED: StoragePolicy.APP_ENCRYPTED,
        PayloadKind.REPORT_PDF: StoragePolicy.APP_ENCRYPTED,
        PayloadKind.METADATA: StoragePolicy.APP_ENCRYPTED,
        PayloadKind.SIDECAR: StoragePolicy.APP_ENCRYPTED,
        PayloadKind.MANIFEST: StoragePolicy.APP_ENCRYPTED,
    },
    StorageProfile.FS_ENCRYPTED_STREAMING: {
        PayloadKind.VIDEO_RAW: StoragePolicy.APP_ENCRYPTED,
        PayloadKind.VIDEO_PROCESSED: StoragePolicy.FS_STREAMABLE,
        PayloadKind.REPORT_PDF: StoragePolicy.APP_ENCRYPTED,
        PayloadKind.METADATA: StoragePolicy.APP_ENCRYPTED,
        PayloadKind.SIDECAR: StoragePolicy.APP_ENCRYPTED,
        PayloadKind.MANIFEST: StoragePolicy.APP_ENCRYPTED,
    },
    StorageProfile.HYBRID_DEFAULT: {
        PayloadKind.VIDEO_RAW: StoragePolicy.APP_ENCRYPTED,
        PayloadKind.VIDEO_PROCESSED: StoragePolicy.FS_STREAMABLE,
        PayloadKind.REPORT_PDF: StoragePolicy.APP_ENCRYPTED,
        PayloadKind.METADATA: StoragePolicy.APP_ENCRYPTED,
        PayloadKind.SIDECAR: StoragePolicy.APP_ENCRYPTED,
        PayloadKind.MANIFEST: StoragePolicy.APP_ENCRYPTED,
    },
}


def _bool_env(name: str) -> bool | None:
    from endoreg_db.config.env import env_str

    raw = env_str(name, "")
    if raw == "":
        return None
    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return None


def _emit_legacy_profile_warning(message: str) -> None:
    if _STATE["legacy_profile_warning_emitted"]:
        return
    logger.warning(message)
    _STATE["legacy_profile_warning_emitted"] = True


def infer_storage_profile_from_legacy_env() -> StorageProfile:
    legacy_encrypted_storage = _bool_env("LX_ANNOTATE_USE_ENCRYPTED_STORAGE")
    if legacy_encrypted_storage is False:
        _emit_legacy_profile_warning(
            "LX_ANNOTATE_USE_ENCRYPTED_STORAGE is deprecated; mapping legacy "
            "disabled encrypted storage mode to ENDOREG_STORAGE_PROFILE="
            "fs_encrypted_streaming."
        )
        return StorageProfile.FS_ENCRYPTED_STREAMING
    if legacy_encrypted_storage is True:
        _emit_legacy_profile_warning(
            "LX_ANNOTATE_USE_ENCRYPTED_STORAGE is deprecated; mapping legacy "
            "enabled encrypted storage mode to ENDOREG_STORAGE_PROFILE="
            "hybrid_default."
        )
        return StorageProfile.HYBRID_DEFAULT
    return StorageProfile.HYBRID_DEFAULT


def get_storage_profile() -> StorageProfile:
    explicit_profile = get_endoreg_storage_profile_name()
    if explicit_profile:
        return StorageProfile(explicit_profile)
    return infer_storage_profile_from_legacy_env()


def resolve_storage_policy(
    payload_kind: PayloadKind | str,
    *,
    profile: StorageProfile | str | None = None,
) -> StoragePolicy:
    resolved_profile = (
        get_storage_profile() if profile is None else StorageProfile(profile)
    )
    resolved_kind = PayloadKind(payload_kind)
    return PROFILE_POLICY_MAP[resolved_profile][resolved_kind]


def prefers_fs_streamable_video_storage(
    *, profile: StorageProfile | str | None = None
) -> bool:
    return (
        resolve_storage_policy(
            PayloadKind.VIDEO_RAW,
            profile=profile,
        )
        == StoragePolicy.FS_STREAMABLE
    )


def requires_app_encrypted_storage(
    payload_kind: PayloadKind | str,
    *,
    profile: StorageProfile | str | None = None,
) -> bool:
    return (
        resolve_storage_policy(payload_kind, profile=profile)
        == StoragePolicy.APP_ENCRYPTED
    )


def storage_profile_warning(profile: StorageProfile | str | None = None) -> str | None:
    resolved_profile = (
        get_storage_profile() if profile is None else StorageProfile(profile)
    )
    if resolved_profile == StorageProfile.STRICT_APP_ENCRYPTED:
        return (
            "ENDOREG_STORAGE_PROFILE=strict_app_encrypted forces videos onto "
            "application-layer storage and may degrade large-video playback or "
            "stream-preparation throughput."
        )
    return None
