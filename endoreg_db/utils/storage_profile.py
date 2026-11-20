from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType

from endoreg_db.config.env import get_endoreg_storage_profile_name
from endoreg_db.utils.rust_backend import storage_profile_policy_rows


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


@dataclass(frozen=True, slots=True)
class StorageProfileState:
    profile: StorageProfile
    policies: Mapping[PayloadKind, StoragePolicy]

    def policy_for(self, payload_kind: PayloadKind | str) -> StoragePolicy:
        return self.policies[PayloadKind(payload_kind)]

    def requires_app_encrypted(self, payload_kind: PayloadKind | str) -> bool:
        return self.policy_for(payload_kind) == StoragePolicy.APP_ENCRYPTED

    def prefers_fs_streamable_video_storage(self) -> bool:
        return self.policy_for(PayloadKind.VIDEO_RAW) == StoragePolicy.FS_STREAMABLE


def _load_profile_policy_map_from_rust() -> Mapping[
    StorageProfile, Mapping[PayloadKind, StoragePolicy]
]:
    rows = storage_profile_policy_rows()
    if rows is None:
        raise RuntimeError(
            "Rust storage profile policy table is unavailable. "
            "Storage routing requires endoreg_rust_backend."
        )

    mutable_map: dict[StorageProfile, dict[PayloadKind, StoragePolicy]] = {}
    for profile_value, payload_kind_value, storage_policy_value in rows:
        profile = StorageProfile(profile_value)
        payload_kind = PayloadKind(payload_kind_value)
        storage_policy = StoragePolicy(storage_policy_value)
        profile_policies = mutable_map.setdefault(profile, {})
        if payload_kind in profile_policies:
            raise RuntimeError(
                "Duplicate Rust storage profile policy row for "
                f"profile={profile.value} payload_kind={payload_kind.value}"
            )
        profile_policies[payload_kind] = storage_policy

    return MappingProxyType(
        {
            profile: MappingProxyType(dict(policies))
            for profile, policies in mutable_map.items()
        }
    )


PROFILE_POLICY_MAP = _load_profile_policy_map_from_rust()


def _validate_profile_policy_map() -> None:
    required_payload_kinds = set(PayloadKind)
    for profile in StorageProfile:
        policies = PROFILE_POLICY_MAP.get(profile)
        if policies is None:
            raise RuntimeError(f"Missing storage policies for profile {profile.value}")
        missing = required_payload_kinds.difference(policies)
        extra = set(policies).difference(required_payload_kinds)
        if missing or extra:
            missing_text = ", ".join(sorted(kind.value for kind in missing)) or "none"
            extra_text = ", ".join(sorted(kind.value for kind in extra)) or "none"
            raise RuntimeError(
                "Storage profile policy map is not exhaustive for "
                f"{profile.value}: missing={missing_text} extra={extra_text}"
            )


_validate_profile_policy_map()


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


def infer_storage_profile_from_legacy_env() -> StorageProfile:
    legacy_encrypted_storage = _bool_env("LX_ANNOTATE_USE_ENCRYPTED_STORAGE")
    if legacy_encrypted_storage is False:
        return StorageProfile.FS_ENCRYPTED_STREAMING
    if legacy_encrypted_storage is True:
        return StorageProfile.HYBRID_DEFAULT
    return StorageProfile.HYBRID_DEFAULT


def get_storage_profile() -> StorageProfile:
    explicit_profile = get_endoreg_storage_profile_name()
    if explicit_profile:
        return StorageProfile(explicit_profile)
    return infer_storage_profile_from_legacy_env()


def resolve_storage_profile_state(
    profile: StorageProfile | str | None = None,
) -> StorageProfileState:
    resolved_profile = (
        get_storage_profile() if profile is None else StorageProfile(profile)
    )
    return StorageProfileState(
        profile=resolved_profile,
        policies=MappingProxyType(dict(PROFILE_POLICY_MAP[resolved_profile])),
    )


def resolve_storage_policy(
    payload_kind: PayloadKind | str,
    *,
    profile: StorageProfile | str | None = None,
) -> StoragePolicy:
    return resolve_storage_profile_state(profile).policy_for(payload_kind)


def prefers_fs_streamable_video_storage(
    *, profile: StorageProfile | str | None = None
) -> bool:
    return resolve_storage_profile_state(profile).prefers_fs_streamable_video_storage()


def requires_app_encrypted_storage(
    payload_kind: PayloadKind | str,
    *,
    profile: StorageProfile | str | None = None,
) -> bool:
    return resolve_storage_profile_state(profile).requires_app_encrypted(payload_kind)


def storage_profile_warning(profile: StorageProfile | str | None = None) -> str | None:
    resolved_profile = resolve_storage_profile_state(profile).profile
    if resolved_profile == StorageProfile.STRICT_APP_ENCRYPTED:
        return (
            "ENDOREG_STORAGE_PROFILE=strict_app_encrypted forces videos onto "
            "application-layer storage and may degrade large-video playback or "
            "stream-preparation throughput."
        )
    return None
