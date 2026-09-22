from __future__ import annotations

import pytest
from pytest import MonkeyPatch

from endoreg_db.utils.storage_profile import (
    PROFILE_POLICY_MAP,
    PayloadKind,
    StoragePolicy,
    StorageProfile,
    get_storage_profile,
    resolve_storage_profile_state,
    resolve_storage_policy,
    storage_profile_warning,
)


def test_storage_profile_defaults_to_hybrid(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.delenv("ENDOREG_STORAGE_PROFILE", raising=False)
    monkeypatch.delenv("LX_ANNOTATE_USE_ENCRYPTED_STORAGE", raising=False)

    assert get_storage_profile() == StorageProfile.HYBRID_DEFAULT
    assert resolve_storage_policy(PayloadKind.VIDEO_RAW) == StoragePolicy.APP_ENCRYPTED
    assert (
        resolve_storage_policy(PayloadKind.VIDEO_PROCESSED)
        == StoragePolicy.FS_STREAMABLE
    )
    assert resolve_storage_policy(PayloadKind.REPORT_PDF) == StoragePolicy.APP_ENCRYPTED


def test_storage_profile_explicit_strict_profile_routes_videos_to_app_encrypted(
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setenv("ENDOREG_STORAGE_PROFILE", "strict_app_encrypted")
    monkeypatch.setenv("LX_ANNOTATE_USE_ENCRYPTED_STORAGE", "0")

    assert get_storage_profile() == StorageProfile.STRICT_APP_ENCRYPTED
    assert resolve_storage_policy(PayloadKind.VIDEO_RAW) == StoragePolicy.APP_ENCRYPTED
    assert (
        resolve_storage_policy(PayloadKind.VIDEO_PROCESSED)
        == StoragePolicy.APP_ENCRYPTED
    )
    assert resolve_storage_policy(PayloadKind.REPORT_PDF) == StoragePolicy.APP_ENCRYPTED


def test_storage_profile_legacy_env_maps_to_fs_streaming(
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.delenv("ENDOREG_STORAGE_PROFILE", raising=False)
    monkeypatch.setenv("LX_ANNOTATE_USE_ENCRYPTED_STORAGE", "0")

    assert get_storage_profile() == StorageProfile.FS_ENCRYPTED_STREAMING
    assert resolve_storage_policy(PayloadKind.VIDEO_RAW) == StoragePolicy.FS_STREAMABLE
    assert (
        resolve_storage_policy(PayloadKind.VIDEO_PROCESSED)
        == StoragePolicy.FS_STREAMABLE
    )
    assert resolve_storage_policy(PayloadKind.REPORT_PDF) == StoragePolicy.APP_ENCRYPTED


def test_storage_profile_warning_only_applies_to_strict_app_profile() -> None:
    assert storage_profile_warning(StorageProfile.STRICT_APP_ENCRYPTED) is not None
    assert storage_profile_warning(StorageProfile.HYBRID_DEFAULT) is None


def test_storage_profile_state_is_typed_and_exhaustive() -> None:
    state = resolve_storage_profile_state(StorageProfile.FS_ENCRYPTED_STREAMING)

    assert state.profile == StorageProfile.FS_ENCRYPTED_STREAMING
    assert set(state.policies) == set(PayloadKind)
    assert state.policy_for(PayloadKind.VIDEO_RAW) == StoragePolicy.FS_STREAMABLE
    assert state.prefers_fs_streamable_video_storage()
    assert state.requires_app_encrypted(PayloadKind.REPORT_PDF)


def test_storage_profile_policy_map_is_immutable() -> None:
    with pytest.raises(TypeError):
        PROFILE_POLICY_MAP[StorageProfile.HYBRID_DEFAULT] = {}  # type: ignore[index]

    with pytest.raises(TypeError):
        PROFILE_POLICY_MAP[StorageProfile.HYBRID_DEFAULT][PayloadKind.VIDEO_RAW] = (  # type: ignore[index]
            StoragePolicy.FS_STREAMABLE
        )
