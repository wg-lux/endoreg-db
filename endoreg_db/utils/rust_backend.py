from __future__ import annotations

import logging
from importlib import import_module
from pathlib import Path
from typing import Callable, Literal, Protocol, Sequence, cast

logger = logging.getLogger(__name__)


class NativeBatchProcessor(Protocol):
    @property
    def worker_count(self) -> int: ...

    def stable_file_identities(
        self,
        paths: list[Path],
        chunk_size: int = 1024 * 1024,
    ) -> list[tuple[int, int, str]]: ...


class _NativeBatchProcessorFactory(Protocol):
    def __call__(self, worker_count: int) -> NativeBatchProcessor: ...


_batch_processor_factory: _NativeBatchProcessorFactory | None
_parse_extracted_frame_numbers: Callable[[list[str]], list[int]] | None
_build_expected_frame_records: Callable[[int, str], list[tuple[int, str]]] | None
_build_frame_records: Callable[..., list[tuple[int, str]]] | None
_render_single_page_pdf: Callable[[str], bytes] | None
_sha256_file_hex: Callable[[Path, int], str] | None
_stable_file_identity: Callable[[Path, int], tuple[int, int, str]] | None
_stable_snapshot_to_path: Callable[[Path, Path, int], tuple[int, int, str]] | None
_native_capabilities: Callable[[], list[tuple[str, str, str]]] | None
_encryption_status: Callable[[Path], str] | None
_is_lx_encrypted_file: Callable[[Path], bool] | None
_decrypt_encrypted_file_range: Callable[[Path, bytes, int, int], bytes] | None
_copy_file_descriptor_to_path: Callable[[int, Path, int], int] | None
_derive_anonymization_status: (
    Callable[[bool, bool, bool, bool, bool, bool, bool], str] | None
)
_derive_report_anonymization_status: (
    Callable[[bool, bool, bool, bool, bool], str] | None
)
_derive_hls_reservation_action: Callable[[str, bool, bool, bool], str] | None
_derive_hls_publication_action: Callable[[str, bool, bool, bool], str] | None
_derive_hls_reconciliation_action: Callable[[str, bool], str] | None
_transition_service_lifecycle: Callable[[str, str], str] | None
_transition_operation_lifecycle: Callable[[str, str], str] | None
_derive_segment_annotation_status: Callable[[bool, bool, bool], str] | None
_derive_frame_annotation_status: Callable[[bool, bool, bool, bool, bool], str] | None
_normalize_frame_task_mode_token: Callable[[str], str] | None
_normalize_frame_sampling_strategy_token: Callable[[str], str] | None
_storage_profile_policy_rows: Callable[[], list[tuple[str, str, str]]] | None
_rust_backend_available = False

try:
    rust_backend = import_module("endoreg_db.endoreg_rust_backend")
    _batch_processor_factory = cast(
        _NativeBatchProcessorFactory | None,
        getattr(rust_backend, "BatchProcessor", None),
    )
    _build_expected_frame_records = getattr(
        rust_backend, "build_expected_frame_records", None
    )
    _build_frame_records = getattr(rust_backend, "build_frame_records", None)
    _parse_extracted_frame_numbers = getattr(
        rust_backend, "parse_extracted_frame_numbers", None
    )
    _render_single_page_pdf = getattr(rust_backend, "render_single_page_pdf", None)
    _derive_anonymization_status = getattr(
        rust_backend, "derive_anonymization_status", None
    )
    _derive_report_anonymization_status = getattr(
        rust_backend, "derive_report_anonymization_status", None
    )
    _derive_hls_reservation_action = getattr(
        rust_backend, "derive_hls_reservation_action", None
    )
    _derive_hls_publication_action = getattr(
        rust_backend, "derive_hls_publication_action", None
    )
    _derive_hls_reconciliation_action = getattr(
        rust_backend, "derive_hls_reconciliation_action", None
    )
    _transition_service_lifecycle = getattr(
        rust_backend, "transition_service_lifecycle", None
    )
    _transition_operation_lifecycle = getattr(
        rust_backend, "transition_operation_lifecycle", None
    )
    _derive_segment_annotation_status = getattr(
        rust_backend, "derive_segment_annotation_status", None
    )
    _derive_frame_annotation_status = getattr(
        rust_backend, "derive_frame_annotation_status", None
    )
    _normalize_frame_task_mode_token = getattr(
        rust_backend, "normalize_frame_task_mode_token", None
    )
    _normalize_frame_sampling_strategy_token = getattr(
        rust_backend, "normalize_frame_sampling_strategy_token", None
    )
    _sha256_file_hex = getattr(rust_backend, "sha256_file_hex", None)
    _stable_file_identity = getattr(rust_backend, "stable_file_identity", None)
    _stable_snapshot_to_path = getattr(rust_backend, "stable_snapshot_to_path", None)
    _native_capabilities = getattr(rust_backend, "native_capabilities", None)
    _encryption_status = getattr(rust_backend, "encryption_status", None)
    _is_lx_encrypted_file = getattr(rust_backend, "is_lx_encrypted_file", None)
    _decrypt_encrypted_file_range = getattr(
        rust_backend,
        "decrypt_encrypted_file_range",
        None,
    )
    _copy_file_descriptor_to_path = getattr(
        rust_backend, "copy_file_descriptor_to_path", None
    )
    _storage_profile_policy_rows = getattr(
        rust_backend, "storage_profile_policy_rows", None
    )

    _rust_backend_available = True
except Exception as exc:
    logger.debug("Rust backend unavailable, using Python fallbacks: %s", exc)
    _batch_processor_factory = None
    _build_expected_frame_records = None
    _build_frame_records = None
    _parse_extracted_frame_numbers = None
    _render_single_page_pdf = None
    _sha256_file_hex = None
    _stable_file_identity = None
    _stable_snapshot_to_path = None
    _native_capabilities = None
    _encryption_status = None
    _is_lx_encrypted_file = None
    _decrypt_encrypted_file_range = None
    _copy_file_descriptor_to_path = None
    _derive_anonymization_status = None
    _derive_report_anonymization_status = None
    _derive_hls_reservation_action = None
    _derive_hls_publication_action = None
    _derive_hls_reconciliation_action = None
    _transition_service_lifecycle = None
    _transition_operation_lifecycle = None
    _derive_segment_annotation_status = None
    _derive_frame_annotation_status = None
    _normalize_frame_task_mode_token = None
    _normalize_frame_sampling_strategy_token = None
    _storage_profile_policy_rows = None
    _rust_backend_available = False

RUST_BACKEND_AVAILABLE: bool = _rust_backend_available


def native_capabilities() -> tuple[tuple[str, str, str], ...]:
    """Return versioned capabilities exposed by the loaded native extension."""
    if _native_capabilities is None:
        return ()
    try:
        rows = _native_capabilities()
    except (OSError, RuntimeError, TypeError, ValueError, OverflowError) as exc:
        raise RuntimeError(f"Rust native_capabilities failed: {exc}") from exc
    return tuple(
        (str(name), str(contract), str(version)) for name, contract, version in rows
    )


def has_native_capability(name: str, contract_version: str) -> bool:
    return any(
        capability_name == name and capability_contract == contract_version
        for (
            capability_name,
            capability_contract,
            _implementation_version,
        ) in native_capabilities()
    )


def native_capability_version(name: str, contract_version: str) -> str | None:
    for (
        capability_name,
        capability_contract,
        implementation_version,
    ) in native_capabilities():
        if capability_name == name and capability_contract == contract_version:
            return implementation_version
    return None


def sha256_file_hex(path: Path, chunk_size: int) -> str | None:
    if _sha256_file_hex is None:
        return None
    try:
        return _sha256_file_hex(Path(path), chunk_size)
    except (OSError, RuntimeError, TypeError, ValueError, OverflowError) as exc:
        logger.warning("Rust sha256_file_hex failed, falling back to Python: %s", exc)
        return None


def stable_file_identity(
    path: Path, chunk_size: int = 1024 * 1024
) -> tuple[int, int, str] | None:
    """Return a stable native file snapshot, or ``None`` without native support."""
    if _stable_file_identity is None:
        return None
    try:
        size_bytes, modified_time_ns, sha256 = _stable_file_identity(
            Path(path), chunk_size
        )
    except (OSError, RuntimeError, TypeError, ValueError, OverflowError) as exc:
        raise RuntimeError(
            f"Rust stable_file_identity failed for {Path(path)}: {exc}"
        ) from exc
    return int(size_bytes), int(modified_time_ns), str(sha256)


def stable_file_identities(
    paths: Sequence[Path],
    *,
    worker_count: int,
    chunk_size: int = 1024 * 1024,
) -> tuple[tuple[int, int, str], ...] | None:
    """
    Derive stable identities concurrently in a bounded native Rayon pool.

    ``None`` means that the loaded extension predates this optional capability.
    Native integrity and mutation failures propagate instead of falling back.
    """
    if worker_count < 1:
        raise ValueError("worker_count must be greater than zero")
    if chunk_size < 1:
        raise ValueError("chunk_size must be greater than zero")
    if _batch_processor_factory is None:
        logger.warning(
            "Rust BatchProcessor is unavailable; caller must use the observable "
            "sequential file-identity path."
        )
        return None
    try:
        processor = _batch_processor_factory(worker_count)
        rows = processor.stable_file_identities(
            [Path(path) for path in paths],
            chunk_size,
        )
    except (OSError, RuntimeError, TypeError, ValueError, OverflowError) as exc:
        raise RuntimeError(f"Rust batch stable file identity failed: {exc}") from exc
    return tuple(
        (int(size_bytes), int(modified_time_ns), str(sha256))
        for size_bytes, modified_time_ns, sha256 in rows
    )


def stable_snapshot_to_path(
    source_path: Path,
    target_path: Path,
    chunk_size: int = 1024 * 1024,
) -> tuple[int, int, str] | None:
    """Copy and hash one stable source view with the native backend."""
    if _stable_snapshot_to_path is None:
        return None
    try:
        size_bytes, modified_time_ns, sha256 = _stable_snapshot_to_path(
            Path(source_path),
            Path(target_path),
            chunk_size,
        )
    except (OSError, RuntimeError, TypeError, ValueError, OverflowError) as exc:
        raise RuntimeError(
            "Rust stable_snapshot_to_path failed for "
            f"{Path(source_path)} -> {Path(target_path)}: {exc}"
        ) from exc
    return int(size_bytes), int(modified_time_ns), str(sha256)


def encryption_status(path: Path) -> str | None:
    if _encryption_status is None:
        return None
    try:
        status = _encryption_status(Path(path))
    except (OSError, RuntimeError, TypeError, ValueError, OverflowError) as exc:
        logger.warning("Rust encryption_status failed, falling back to Python: %s", exc)
        return None
    return status if status in {"encrypted", "plaintext"} else None


def is_lx_encrypted_file(path: Path) -> bool | None:
    if _is_lx_encrypted_file is None:
        return None
    try:
        return bool(_is_lx_encrypted_file(Path(path)))
    except (OSError, RuntimeError, TypeError, ValueError, OverflowError) as exc:
        logger.warning(
            "Rust is_lx_encrypted_file failed, falling back to Python: %s", exc
        )
        return None


def decrypt_encrypted_file_range(
    *,
    path: Path,
    master_key: bytes,
    start: int,
    end: int,
) -> bytes | None:
    """Decrypt one bounded file range natively, or report unavailable native code."""
    if _decrypt_encrypted_file_range is None:
        return None
    try:
        return bytes(
            _decrypt_encrypted_file_range(
                Path(path),
                master_key,
                start,
                end,
            )
        )
    except (OSError, RuntimeError, TypeError, ValueError, OverflowError) as exc:
        raise RuntimeError(
            f"Rust encrypted range decryption failed for {Path(path)}: {exc}"
        ) from exc


def copy_file_descriptor_to_path(
    *,
    source_fd: int,
    target_path: Path,
    chunk_size: int,
) -> int | None:
    if _copy_file_descriptor_to_path is None:
        return None
    try:
        return int(
            _copy_file_descriptor_to_path(
                source_fd,
                Path(target_path),
                chunk_size,
            )
        )
    except (OSError, RuntimeError, TypeError, ValueError, OverflowError) as exc:
        logger.warning(
            "Rust copy_file_descriptor_to_path failed, falling back to Python: %s",
            exc,
        )
        return None


def render_single_page_pdf(text: str) -> bytes | None:
    if _render_single_page_pdf is None:
        return None
    try:
        return bytes(_render_single_page_pdf(text))
    except (OSError, RuntimeError, TypeError, ValueError, OverflowError) as exc:
        logger.warning(
            "Rust render_single_page_pdf failed, falling back to Python: %s", exc
        )
        return None


def parse_extracted_frame_numbers(paths: Sequence[Path]) -> list[int] | None:
    if _parse_extracted_frame_numbers is None:
        return None
    try:
        return list(_parse_extracted_frame_numbers([str(path) for path in paths]))
    except (OSError, RuntimeError, TypeError, ValueError, OverflowError) as exc:
        logger.warning(
            "Rust parse_extracted_frame_numbers failed, falling back to Python: %s",
            exc,
        )
        return None


def build_frame_records(
    paths: Sequence[Path],
    *,
    relative_to: Path | None = None,
    zero_based: bool = False,
) -> list[tuple[int, str]] | None:
    if _build_frame_records is None:
        return None
    try:
        return list(
            _build_frame_records(
                [str(path) for path in paths],
                relative_to=str(relative_to) if relative_to is not None else None,
                zero_based=zero_based,
            )
        )
    except (OSError, RuntimeError, TypeError, ValueError, OverflowError) as exc:
        logger.warning(
            "Rust build_frame_records failed, falling back to Python: %s", exc
        )
        return None


def build_expected_frame_records(
    frame_count: int, ext: str = "jpg"
) -> list[tuple[int, str]] | None:
    if _build_expected_frame_records is None:
        return None
    try:
        return list(_build_expected_frame_records(frame_count, ext))
    except (OSError, RuntimeError, TypeError, ValueError, OverflowError) as exc:
        logger.warning(
            "Rust build_expected_frame_records failed, falling back to Python: %s",
            exc,
        )
        return None


def derive_anonymization_status(
    *,
    processing_error: bool,
    anonymization_validated: bool,
    sensitive_meta_processed: bool,
    frames_extracted: bool,
    anonymized: bool,
    was_created: bool,
    processing_started: bool,
) -> str | None:
    if _derive_anonymization_status is None:
        return None
    try:
        return _derive_anonymization_status(
            processing_error,
            anonymization_validated,
            sensitive_meta_processed,
            frames_extracted,
            anonymized,
            was_created,
            processing_started,
        )
    except (OSError, RuntimeError, TypeError, ValueError, OverflowError) as exc:
        logger.warning(
            "Rust derive_anonymization_status failed, falling back to Python: %s",
            exc,
        )
        return None


def derive_report_anonymization_status(
    *,
    processing_error: bool,
    anonymization_validated: bool,
    sensitive_meta_processed: bool,
    anonymized: bool,
    processing_started: bool,
) -> str | None:
    if _derive_report_anonymization_status is None:
        return None
    try:
        return _derive_report_anonymization_status(
            processing_error,
            anonymization_validated,
            sensitive_meta_processed,
            anonymized,
            processing_started,
        )
    except (OSError, RuntimeError, TypeError, ValueError, OverflowError) as exc:
        logger.warning(
            "Rust derive_report_anonymization_status failed, falling back to Python: %s",
            exc,
        )
        return None


HlsReservationAction = Literal[
    "queue",
    "already_ready",
    "already_in_flight",
]
HlsPublicationAction = Literal[
    "publish_initial",
    "replace_ready",
    "defer",
    "reject",
]
HlsReconciliationAction = Literal["preserve", "fail_and_cleanup"]

_HLS_IN_FLIGHT_STATUSES = frozenset({"queued", "materializing", "validated"})
_HLS_RESERVATION_ACTIONS = frozenset({"queue", "already_ready", "already_in_flight"})
_HLS_PUBLICATION_ACTIONS = frozenset(
    {"publish_initial", "replace_ready", "defer", "reject"}
)
_HLS_RECONCILIATION_ACTIONS = frozenset({"preserve", "fail_and_cleanup"})


def _validate_hls_in_flight_status(status: str) -> None:
    if status and status not in _HLS_IN_FLIGHT_STATUSES:
        raise ValueError(f"unsupported HLS in-flight status: {status}")


def derive_hls_reservation_action(
    *,
    active_status: str,
    active_is_stale: bool,
    ready_matches_source: bool,
    force: bool,
) -> HlsReservationAction:
    _validate_hls_in_flight_status(active_status)
    if active_status and active_is_stale:
        return "queue"
    if _derive_hls_reservation_action is None:
        if active_status:
            return "already_in_flight"
        if ready_matches_source and not force:
            return "already_ready"
        return "queue"
    try:
        action = str(
            _derive_hls_reservation_action(
                active_status,
                active_is_stale,
                ready_matches_source,
                force,
            )
        )
    except (OSError, RuntimeError, TypeError, ValueError, OverflowError) as exc:
        raise RuntimeError(f"Rust derive_hls_reservation_action failed: {exc}") from exc
    if action not in _HLS_RESERVATION_ACTIONS:
        raise RuntimeError(
            f"Rust returned unsupported HLS reservation action: {action}"
        )
    return cast(HlsReservationAction, action)


def derive_hls_publication_action(
    *,
    attempt_status: str,
    owner_matches: bool,
    has_active_lease: bool,
    has_ready_generation: bool,
) -> HlsPublicationAction:
    _validate_hls_in_flight_status(attempt_status)
    if _derive_hls_publication_action is None:
        if attempt_status != "validated" or not owner_matches:
            return "reject"
        if has_active_lease:
            return "defer"
        return "replace_ready" if has_ready_generation else "publish_initial"
    try:
        action = str(
            _derive_hls_publication_action(
                attempt_status,
                owner_matches,
                has_active_lease,
                has_ready_generation,
            )
        )
    except (OSError, RuntimeError, TypeError, ValueError, OverflowError) as exc:
        raise RuntimeError(f"Rust derive_hls_publication_action failed: {exc}") from exc
    if action not in _HLS_PUBLICATION_ACTIONS:
        raise RuntimeError(
            f"Rust returned unsupported HLS publication action: {action}"
        )
    return cast(HlsPublicationAction, action)


def derive_hls_reconciliation_action(
    *,
    status: str,
    is_stale: bool,
) -> HlsReconciliationAction:
    _validate_hls_in_flight_status(status)
    if _derive_hls_reconciliation_action is None:
        return (
            "fail_and_cleanup"
            if status in _HLS_IN_FLIGHT_STATUSES and is_stale
            else "preserve"
        )
    try:
        action = str(_derive_hls_reconciliation_action(status, is_stale))
    except (OSError, RuntimeError, TypeError, ValueError, OverflowError) as exc:
        raise RuntimeError(
            f"Rust derive_hls_reconciliation_action failed: {exc}"
        ) from exc
    if action not in _HLS_RECONCILIATION_ACTIONS:
        raise RuntimeError(
            f"Rust returned unsupported HLS reconciliation action: {action}"
        )
    return cast(HlsReconciliationAction, action)


ServiceLifecycleStateToken = Literal[
    "stopped",
    "starting",
    "running",
    "degraded",
    "stopping",
    "failed",
    "lost",
]
ServiceLifecycleEventToken = Literal[
    "start_requested",
    "start_succeeded",
    "start_failed",
    "health_degraded",
    "health_restored",
    "stop_requested",
    "stop_succeeded",
    "stop_failed",
    "runtime_failed",
    "ownership_lost",
    "reconcile_stopped",
]
OperationLifecycleStateToken = Literal[
    "queued",
    "claimed",
    "running",
    "retry_wait",
    "succeeded",
    "failed",
    "cancelled",
    "lost",
]
OperationLifecycleEventToken = Literal[
    "claim",
    "start",
    "succeed",
    "fail",
    "retry_scheduled",
    "retry_ready",
    "retry_requested",
    "cancel",
    "ownership_lost",
    "integrity_lost",
    "reconcile_retry",
    "reconcile_fail",
]

_SERVICE_LIFECYCLE_STATES = frozenset(
    {
        "stopped",
        "starting",
        "running",
        "degraded",
        "stopping",
        "failed",
        "lost",
    }
)
_SERVICE_LIFECYCLE_EVENTS = frozenset(
    {
        "start_requested",
        "start_succeeded",
        "start_failed",
        "health_degraded",
        "health_restored",
        "stop_requested",
        "stop_succeeded",
        "stop_failed",
        "runtime_failed",
        "ownership_lost",
        "reconcile_stopped",
    }
)
_OPERATION_LIFECYCLE_STATES = frozenset(
    {
        "queued",
        "claimed",
        "running",
        "retry_wait",
        "succeeded",
        "failed",
        "cancelled",
        "lost",
    }
)
_OPERATION_LIFECYCLE_EVENTS = frozenset(
    {
        "claim",
        "start",
        "succeed",
        "fail",
        "retry_scheduled",
        "retry_ready",
        "retry_requested",
        "cancel",
        "ownership_lost",
        "integrity_lost",
        "reconcile_retry",
        "reconcile_fail",
    }
)
_LIFECYCLE_STATE_MACHINE_CAPABILITY = "lifecycle_state_machine"
_LIFECYCLE_STATE_MACHINE_CONTRACT = "lifecycle_state_v3"


def _require_lifecycle_state_machine_capability() -> None:
    if (
        _transition_service_lifecycle is None
        or _transition_operation_lifecycle is None
        or not has_native_capability(
            _LIFECYCLE_STATE_MACHINE_CAPABILITY,
            _LIFECYCLE_STATE_MACHINE_CONTRACT,
        )
    ):
        raise RuntimeError(
            "required Rust lifecycle_state_machine capability is unavailable"
        )


def transition_service_lifecycle(
    *,
    current_state: ServiceLifecycleStateToken,
    event: ServiceLifecycleEventToken,
) -> ServiceLifecycleStateToken:
    if current_state not in _SERVICE_LIFECYCLE_STATES:
        raise ValueError(f"unsupported service lifecycle state: {current_state}")
    if event not in _SERVICE_LIFECYCLE_EVENTS:
        raise ValueError(f"unsupported service lifecycle event: {event}")
    _require_lifecycle_state_machine_capability()
    assert _transition_service_lifecycle is not None
    try:
        target_state = str(_transition_service_lifecycle(current_state, event))
    except ValueError as exc:
        raise ValueError(str(exc)) from exc
    except (OSError, RuntimeError, TypeError, OverflowError) as exc:
        raise RuntimeError(f"Rust transition_service_lifecycle failed: {exc}") from exc
    if target_state not in _SERVICE_LIFECYCLE_STATES:
        raise RuntimeError(
            f"Rust returned unsupported service lifecycle state: {target_state}"
        )
    return cast(ServiceLifecycleStateToken, target_state)


def transition_operation_lifecycle(
    *,
    current_state: OperationLifecycleStateToken,
    event: OperationLifecycleEventToken,
) -> OperationLifecycleStateToken:
    if current_state not in _OPERATION_LIFECYCLE_STATES:
        raise ValueError(f"unsupported operation lifecycle state: {current_state}")
    if event not in _OPERATION_LIFECYCLE_EVENTS:
        raise ValueError(f"unsupported operation lifecycle event: {event}")
    _require_lifecycle_state_machine_capability()
    assert _transition_operation_lifecycle is not None
    try:
        target_state = str(_transition_operation_lifecycle(current_state, event))
    except ValueError as exc:
        raise ValueError(str(exc)) from exc
    except (OSError, RuntimeError, TypeError, OverflowError) as exc:
        raise RuntimeError(
            f"Rust transition_operation_lifecycle failed: {exc}"
        ) from exc
    if target_state not in _OPERATION_LIFECYCLE_STATES:
        raise RuntimeError(
            f"Rust returned unsupported operation lifecycle state: {target_state}"
        )
    return cast(OperationLifecycleStateToken, target_state)


def derive_segment_annotation_status(
    *,
    segment_annotations_created: bool,
    segment_annotations_validated: bool,
    outside_segments_removed: bool,
) -> str | None:
    if _derive_segment_annotation_status is None:
        return None
    try:
        return _derive_segment_annotation_status(
            segment_annotations_created,
            segment_annotations_validated,
            outside_segments_removed,
        )
    except (OSError, RuntimeError, TypeError, ValueError, OverflowError) as exc:
        logger.warning(
            "Rust derive_segment_annotation_status failed, falling back to Python: %s",
            exc,
        )
        return None


def derive_frame_annotation_status(
    *,
    has_state: bool,
    frames_extracted: bool,
    initial_prediction_completed: bool,
    lvs_created: bool,
    frame_annotations_generated: bool,
) -> str | None:
    if _derive_frame_annotation_status is None:
        return None
    try:
        return _derive_frame_annotation_status(
            has_state,
            frames_extracted,
            initial_prediction_completed,
            lvs_created,
            frame_annotations_generated,
        )
    except (OSError, RuntimeError, TypeError, ValueError, OverflowError) as exc:
        logger.warning(
            "Rust derive_frame_annotation_status failed, falling back to Python: %s",
            exc,
        )
        return None


def normalize_frame_task_mode_token(value: str) -> str | None:
    if _normalize_frame_task_mode_token is None:
        return None
    try:
        return _normalize_frame_task_mode_token(value)
    except (OSError, RuntimeError, TypeError, ValueError, OverflowError) as exc:
        logger.warning(
            "Rust normalize_frame_task_mode_token failed, falling back to Python: %s",
            exc,
        )
        return None


def normalize_frame_sampling_strategy_token(value: str) -> str | None:
    if _normalize_frame_sampling_strategy_token is None:
        return None
    try:
        return _normalize_frame_sampling_strategy_token(value)
    except (OSError, RuntimeError, TypeError, ValueError, OverflowError) as exc:
        logger.warning(
            "Rust normalize_frame_sampling_strategy_token failed, falling back to Python: %s",
            exc,
        )
        return None


def storage_profile_policy_rows() -> list[tuple[str, str, str]] | None:
    if _storage_profile_policy_rows is None:
        return None
    try:
        return list(_storage_profile_policy_rows())
    except (OSError, RuntimeError, TypeError, ValueError, OverflowError) as exc:
        logger.warning(
            "Rust storage_profile_policy_rows failed; storage routing cannot use Rust table: %s",
            exc,
        )
        return None
