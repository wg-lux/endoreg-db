from __future__ import annotations

import logging
from importlib import import_module
from pathlib import Path
from enum import StrEnum
from typing import Literal, Protocol, cast
from collections.abc import Callable, Sequence

from endoreg_db.config.secret_keyring import configured_master_keys

# Native header values have already passed the shared format validator.
type ParsedEncryptedHeaderMetadata = tuple[
    int, str, int, bytes, bytes, bytes, bytes | None, bytes | None
]

type ParsedEncryptedHeader = tuple[int, str, int, bytes, bytes, bytes]

logger = logging.getLogger(__name__)


class NativeBatchProcessor(Protocol):
    @property
    def worker_count(self) -> int: ...

    def stable_file_identities(
        self,
        paths: list[Path],
        chunk_size: int = 1024 * 1024,
        master_keys: list[bytes] = ...,
    ) -> list[tuple[int, int, str]]: ...


class _NativeBatchProcessorFactory(Protocol):
    def __call__(self, worker_count: int) -> NativeBatchProcessor: ...


class NativeTimelineIndex(Protocol):
    @property
    def content_hash(self) -> str | None: ...

    @property
    def persisted_count(self) -> int: ...

    def frame_number_to_seconds(self, frame_number: int) -> float: ...

    def seconds_to_frame_number(self, timestamp_seconds: float) -> int: ...

    def frame_numbers_to_seconds(self, frame_numbers: list[int]) -> list[float]: ...

    def seconds_to_frame_numbers(self, timestamps: list[float]) -> list[int]: ...


class _NativeTimelineIndexFactory(Protocol):
    def __call__(
        self,
        frame_numbers: list[int],
        timestamps: list[float],
        *,
        fps: float | None,
        frame_count: int | None,
        duration_seconds: float | None,
        requires_persisted_pts: bool,
        first_timestamp_seconds: float,
        content_hash: str | None,
    ) -> NativeTimelineIndex: ...


_batch_processor_factory: _NativeBatchProcessorFactory | None
_timeline_index_factory: _NativeTimelineIndexFactory | None
_compose_frame_ancestry: Callable[[list[int], list[list[int]]], list[int]] | None
_parse_extracted_frame_numbers: Callable[[list[str]], list[int]] | None
_build_expected_frame_records: Callable[[int, str], list[tuple[int, str]]] | None
_build_frame_records: Callable[..., list[tuple[int, str]]] | None
_render_single_page_pdf: Callable[[str], bytes] | None
_stable_file_identity: Callable[[Path, int, list[bytes]], tuple[int, int, str]] | None
_parse_encrypted_header: Callable[[bytes], ParsedEncryptedHeader] | None
_parse_encrypted_header_metadata: (
    Callable[[bytes], ParsedEncryptedHeaderMetadata] | None
)
_encrypted_chunk_context: Callable[[bytes, int, int, bool], tuple[bytes, bytes]] | None
_native_capabilities: Callable[[], list[tuple[str, str, str]]] | None
_encryption_status: Callable[[Path], str] | None
_decrypt_encrypted_file_range: Callable[[Path, bytes, int, int], bytes] | None
_copy_file_descriptor_to_path: Callable[[int, Path, int], int] | None
_anonymization_status_rules: (
    Callable[[bool], list[tuple[str, list[tuple[str, bool]]]]] | None
)
_derive_anonymization_status: (
    Callable[[bool, bool, bool, bool, bool, bool, bool], str] | None
)
_derive_report_anonymization_status: (
    Callable[[bool, bool, bool, bool, bool], str] | None
)
_anonymization_status_phase: Callable[[str], str] | None
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
_native_state_vocabularies: Callable[[], list[tuple[str, list[str]]]] | None
_rust_backend_available = False


class HubTransferEvent(StrEnum):
    """Mirror of the native ``hub_transfer_event`` vocabulary."""

    QUEUE = "queue"
    RETRY = "retry"
    OPERATOR_RETRY = "operator_retry"
    REDISPATCH = "redispatch"
    START_REGISTRATION = "start_registration"
    START_UPLOAD = "start_upload"
    INELIGIBLE = "ineligible"
    ACKNOWLEDGE = "acknowledge"
    FAIL = "fail"


def _require_hub_transfer_state() -> None:
    if not has_native_capability("hub_transfer_state_machine", "hub_transfer_state_v2"):
        raise RuntimeError(
            "Required Rust hub_transfer_state_v2 capability is unavailable"
        )


def reduce_hub_transfer_event(
    *,
    current_state: str,
    current_failure_class: str | None,
    event: HubTransferEvent,
    remote_status: str | None = None,
    failure_class: str | None = None,
) -> tuple[str, str | None, bool]:
    """Native transition ``(status, failure_class, apply)``; caller holds the row lock."""
    _require_hub_transfer_state()
    reduce = cast(
        Callable[
            [str, str | None, str, str | None, str | None],
            tuple[str, str | None, bool],
        ],
        getattr(rust_backend, "reduce_hub_transfer_event"),
    )
    return reduce(
        current_state, current_failure_class, event, remote_status, failure_class
    )


def hub_failure_is_retryable(failure_class: str) -> bool:
    _require_hub_transfer_state()
    classify = cast(
        Callable[[str], bool], getattr(rust_backend, "hub_failure_is_retryable")
    )
    return classify(failure_class)


def import_processing_stack_transition(
    payload: str,
    event: str,
    media_id: int | None = None,
    source_sha256: str | None = None,
) -> str:
    if not has_native_capability(
        "import_processing_stack", "import_processing_stack_v1"
    ):
        raise RuntimeError(
            "Required Rust import_processing_stack_v1 capability is unavailable"
        )
    transition = cast(
        Callable[[str, str, int | None, str | None], str],
        getattr(rust_backend, "transition_import_processing_stack"),
    )
    return transition(payload, event, media_id, source_sha256)


def new_import_processing_stack(reference: str, media_type: str) -> str:
    if not has_native_capability(
        "import_processing_stack", "import_processing_stack_v1"
    ):
        raise RuntimeError(
            "Required Rust import_processing_stack_v1 capability is unavailable"
        )
    create = cast(
        Callable[[str, str], str], getattr(rust_backend, "new_import_processing_stack")
    )
    return create(reference, media_type)


try:
    rust_backend = import_module("endoreg_db.endoreg_rust_backend")
    _batch_processor_factory = cast(
        _NativeBatchProcessorFactory | None,
        getattr(rust_backend, "BatchProcessor", None),
    )
    _timeline_index_factory = cast(
        _NativeTimelineIndexFactory | None,
        getattr(rust_backend, "TimelineIndex", None),
    )
    _compose_frame_ancestry = getattr(rust_backend, "compose_frame_ancestry", None)
    _build_expected_frame_records = getattr(
        rust_backend, "build_expected_frame_records", None
    )
    _build_frame_records = getattr(rust_backend, "build_frame_records", None)
    _parse_extracted_frame_numbers = getattr(
        rust_backend, "parse_extracted_frame_numbers", None
    )
    _render_single_page_pdf = getattr(rust_backend, "render_single_page_pdf", None)
    _anonymization_status_rules = getattr(
        rust_backend, "anonymization_status_rules", None
    )
    _derive_anonymization_status = getattr(
        rust_backend, "derive_anonymization_status", None
    )
    _derive_report_anonymization_status = getattr(
        rust_backend, "derive_report_anonymization_status", None
    )
    _anonymization_status_phase = getattr(
        rust_backend, "anonymization_status_phase", None
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
    _stable_file_identity = getattr(rust_backend, "stable_file_identity", None)
    _parse_encrypted_header = getattr(rust_backend, "parse_encrypted_header", None)
    _parse_encrypted_header_metadata = getattr(
        rust_backend, "parse_encrypted_header_metadata", None
    )
    _encrypted_chunk_context = getattr(rust_backend, "encrypted_chunk_context", None)
    _native_capabilities = getattr(rust_backend, "native_capabilities", None)
    _encryption_status = getattr(rust_backend, "encryption_status", None)
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
    _native_state_vocabularies = getattr(
        rust_backend, "native_state_vocabularies", None
    )

    _rust_backend_available = True
except Exception as exc:
    logger.debug("Rust backend unavailable, using Python fallbacks: %s", exc)
    _batch_processor_factory = None
    _timeline_index_factory = None
    _compose_frame_ancestry = None
    _build_expected_frame_records = None
    _build_frame_records = None
    _parse_extracted_frame_numbers = None
    _render_single_page_pdf = None
    _stable_file_identity = None
    _parse_encrypted_header = None
    _parse_encrypted_header_metadata = None
    _encrypted_chunk_context = None
    _native_capabilities = None
    _encryption_status = None
    _decrypt_encrypted_file_range = None
    _copy_file_descriptor_to_path = None
    _anonymization_status_rules = None
    _derive_anonymization_status = None
    _derive_report_anonymization_status = None
    _anonymization_status_phase = None
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
    _native_state_vocabularies = None
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


def stable_file_identity(
    path: Path, chunk_size: int = 1024 * 1024
) -> tuple[int, int, str] | None:
    """Return a stable native file snapshot, or ``None`` without native support."""
    if _stable_file_identity is None:
        return None
    ring = configured_master_keys()
    try:
        size_bytes, modified_time_ns, sha256 = _stable_file_identity(
            Path(path), chunk_size, list(ring.readers) if ring is not None else []
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
    ring = configured_master_keys()
    try:
        processor = _batch_processor_factory(worker_count)
        rows = processor.stable_file_identities(
            [Path(path) for path in paths],
            chunk_size,
            list(ring.readers) if ring is not None else [],
        )
    except (OSError, RuntimeError, TypeError, ValueError, OverflowError) as exc:
        raise RuntimeError(f"Rust batch stable file identity failed: {exc}") from exc
    return tuple(
        (int(size_bytes), int(modified_time_ns), str(sha256))
        for size_bytes, modified_time_ns, sha256 in rows
    )


def parse_encrypted_header(data: bytes) -> ParsedEncryptedHeader:
    if _parse_encrypted_header is None:
        raise RuntimeError(
            "Rust parse_encrypted_header is required for encrypted media"
        )
    return _parse_encrypted_header(data)


def require_encrypted_format_runtime() -> None:
    if _parse_encrypted_header_metadata is None or _encrypted_chunk_context is None:
        raise RuntimeError(
            "LXENC v2 requires matching Python and native backend artifacts; "
            "install or rebuild them together before reading or writing media"
        )


def parse_encrypted_header_metadata(data: bytes) -> ParsedEncryptedHeaderMetadata:
    require_encrypted_format_runtime()
    assert _parse_encrypted_header_metadata is not None
    return _parse_encrypted_header_metadata(data)


def encrypted_chunk_context(
    data: bytes, counter: int, ciphertext_length: int, final: bool
) -> tuple[bytes, bytes]:
    """Use the native format authority for chunk bounds, nonce and associated data."""
    require_encrypted_format_runtime()
    assert _encrypted_chunk_context is not None
    return _encrypted_chunk_context(data, counter, ciphertext_length, final)


def encryption_status(path: Path) -> Literal["encrypted", "plaintext"]:
    """Probe the format through Rust; unreadable is never treated as plaintext."""
    if _encryption_status is None:
        raise RuntimeError(
            "Rust encryption_status is required for encrypted media probes"
        )
    status = _encryption_status(path)
    if status == "encrypted":
        return "encrypted"
    if status == "plaintext":
        return "plaintext"
    raise ValueError("Rust encryption_status returned an unsupported status")


def is_lx_encrypted_file(path: Path) -> bool:
    return encryption_status(path) == "encrypted"


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


def anonymization_status_rules(
    *, report: bool
) -> list[tuple[str, list[tuple[str, bool]]]]:
    if _anonymization_status_rules is None:
        raise RuntimeError("Rust anonymization status rules are unavailable.")
    return _anonymization_status_rules(report)


def derive_anonymization_status(
    *,
    processing_error: bool,
    anonymization_validated: bool,
    sensitive_meta_processed: bool,
    frames_extracted: bool,
    anonymized: bool,
    was_created: bool,
    processing_started: bool,
) -> str:
    if _derive_anonymization_status is None:
        raise RuntimeError("Rust derive_anonymization_status is unavailable.")
    return _derive_anonymization_status(
        processing_error,
        anonymization_validated,
        sensitive_meta_processed,
        frames_extracted,
        anonymized,
        was_created,
        processing_started,
    )


def derive_report_anonymization_status(
    *,
    processing_error: bool,
    anonymization_validated: bool,
    sensitive_meta_processed: bool,
    anonymized: bool,
    processing_started: bool,
) -> str:
    if _derive_report_anonymization_status is None:
        raise RuntimeError("Rust derive_report_anonymization_status is unavailable.")
    return _derive_report_anonymization_status(
        processing_error,
        anonymization_validated,
        sensitive_meta_processed,
        anonymized,
        processing_started,
    )


def anonymization_status_phase(status: str) -> str:
    """Classify a wire status token; unknown tokens raise ValueError in Rust."""
    if _anonymization_status_phase is None:
        raise RuntimeError("Rust anonymization_status_phase is unavailable.")
    return _anonymization_status_phase(status)


def build_timeline_index(
    frame_numbers: list[int],
    timestamps: list[float],
    *,
    fps: float | None,
    frame_count: int | None,
    duration_seconds: float | None,
    requires_persisted_pts: bool,
    first_timestamp_seconds: float,
    content_hash: str | None,
) -> NativeTimelineIndex:
    """Build the native frame/time resolver; invalid coordinates raise ValueError."""
    if _timeline_index_factory is None:
        raise RuntimeError("Rust TimelineIndex is unavailable.")
    return _timeline_index_factory(
        frame_numbers,
        timestamps,
        fps=fps,
        frame_count=frame_count,
        duration_seconds=duration_seconds,
        requires_persisted_pts=requires_persisted_pts,
        first_timestamp_seconds=first_timestamp_seconds,
        content_hash=content_hash,
    )


def compose_frame_ancestry(
    source_frame_counts: list[int], source_frame_indices: list[list[int]]
) -> list[int]:
    """Map final output frames to recording frames; invalid chains raise ValueError."""
    if _compose_frame_ancestry is None:
        raise RuntimeError("Rust compose_frame_ancestry is unavailable.")
    return _compose_frame_ancestry(source_frame_counts, source_frame_indices)


class HlsReservationAction(StrEnum):
    """Mirror of the native ``hls_reservation_action`` vocabulary."""

    ALREADY_READY = "already_ready"
    ALREADY_IN_FLIGHT = "already_in_flight"
    QUEUE = "queue"


class HlsPublicationAction(StrEnum):
    """Mirror of the native ``hls_publication_action`` vocabulary."""

    REJECT = "reject"
    DEFER = "defer"
    REPLACE_READY = "replace_ready"
    PUBLISH_INITIAL = "publish_initial"


class HlsReconciliationAction(StrEnum):
    """Mirror of the native ``hls_reconciliation_action`` vocabulary."""

    FAIL_AND_CLEANUP = "fail_and_cleanup"
    PRESERVE = "preserve"


_NATIVE_DECISION_ERRORS = (OSError, RuntimeError, TypeError, ValueError, OverflowError)


def derive_hls_reservation_action(
    *,
    active_status: str,
    active_is_stale: bool,
    ready_matches_source: bool,
    force: bool,
) -> HlsReservationAction:
    """Native reservation decision; an empty status means no in-flight attempt."""
    if _derive_hls_reservation_action is None:
        raise RuntimeError("Rust derive_hls_reservation_action is unavailable.")
    try:
        action = _derive_hls_reservation_action(
            active_status, active_is_stale, ready_matches_source, force
        )
    except _NATIVE_DECISION_ERRORS as exc:
        raise RuntimeError(f"Rust derive_hls_reservation_action failed: {exc}") from exc
    return HlsReservationAction(action)


def derive_hls_publication_action(
    *,
    attempt_status: str,
    owner_matches: bool,
    has_active_lease: bool,
    has_ready_generation: bool,
) -> HlsPublicationAction:
    """Native publication decision for one validated materialization attempt."""
    if _derive_hls_publication_action is None:
        raise RuntimeError("Rust derive_hls_publication_action is unavailable.")
    try:
        action = _derive_hls_publication_action(
            attempt_status, owner_matches, has_active_lease, has_ready_generation
        )
    except _NATIVE_DECISION_ERRORS as exc:
        raise RuntimeError(f"Rust derive_hls_publication_action failed: {exc}") from exc
    return HlsPublicationAction(action)


def derive_hls_reconciliation_action(
    *,
    status: str,
    is_stale: bool,
) -> HlsReconciliationAction:
    """Native stale-attempt decision; non-in-flight statuses raise in Rust."""
    if _derive_hls_reconciliation_action is None:
        raise RuntimeError("Rust derive_hls_reconciliation_action is unavailable.")
    try:
        action = _derive_hls_reconciliation_action(status, is_stale)
    except _NATIVE_DECISION_ERRORS as exc:
        raise RuntimeError(
            f"Rust derive_hls_reconciliation_action failed: {exc}"
        ) from exc
    return HlsReconciliationAction(action)


_LIFECYCLE_STATE_MACHINE_CAPABILITY = "lifecycle_state_machine"
_LIFECYCLE_STATE_MACHINE_CONTRACT = "lifecycle_state_v3"


def _require_lifecycle_transition(
    transition: Callable[[str, str], str] | None,
) -> Callable[[str, str], str]:
    """Both reducers form one capability; either missing disables both."""
    if (
        transition is None
        or _transition_service_lifecycle is None
        or _transition_operation_lifecycle is None
        or not has_native_capability(
            _LIFECYCLE_STATE_MACHINE_CAPABILITY,
            _LIFECYCLE_STATE_MACHINE_CONTRACT,
        )
    ):
        raise RuntimeError(
            "required Rust lifecycle_state_machine capability is unavailable"
        )
    return transition


def transition_service_lifecycle(*, current_state: str, event: str) -> str:
    """Native service transition; unknown tokens and impossible transitions raise ValueError."""
    transition = _require_lifecycle_transition(_transition_service_lifecycle)
    try:
        return transition(current_state, event)
    except ValueError as exc:
        raise ValueError(str(exc)) from exc
    except (OSError, RuntimeError, TypeError, OverflowError) as exc:
        raise RuntimeError(f"Rust transition_service_lifecycle failed: {exc}") from exc


def transition_operation_lifecycle(*, current_state: str, event: str) -> str:
    """Native operation transition; unknown tokens and impossible transitions raise ValueError."""
    transition = _require_lifecycle_transition(_transition_operation_lifecycle)
    try:
        return transition(current_state, event)
    except ValueError as exc:
        raise ValueError(str(exc)) from exc
    except (OSError, RuntimeError, TypeError, OverflowError) as exc:
        raise RuntimeError(
            f"Rust transition_operation_lifecycle failed: {exc}"
        ) from exc


def derive_segment_annotation_status(
    *,
    segment_annotations_created: bool,
    segment_annotations_validated: bool,
    outside_segments_removed: bool,
) -> str:
    if _derive_segment_annotation_status is None:
        raise RuntimeError("Rust derive_segment_annotation_status is unavailable.")
    return _derive_segment_annotation_status(
        segment_annotations_created,
        segment_annotations_validated,
        outside_segments_removed,
    )


def derive_frame_annotation_status(
    *,
    has_state: bool,
    frames_extracted: bool,
    initial_prediction_completed: bool,
    lvs_created: bool,
    frame_annotations_generated: bool,
) -> str:
    if _derive_frame_annotation_status is None:
        raise RuntimeError("Rust derive_frame_annotation_status is unavailable.")
    return _derive_frame_annotation_status(
        has_state,
        frames_extracted,
        initial_prediction_completed,
        lvs_created,
        frame_annotations_generated,
    )


def normalize_frame_task_mode_token(value: str) -> str:
    """Return the canonical token; unknown task modes raise ValueError in Rust."""
    if _normalize_frame_task_mode_token is None:
        raise RuntimeError("Rust normalize_frame_task_mode_token is unavailable.")
    return _normalize_frame_task_mode_token(value)


def normalize_frame_sampling_strategy_token(value: str) -> str:
    """Return the canonical token; unknown strategies raise ValueError in Rust."""
    if _normalize_frame_sampling_strategy_token is None:
        raise RuntimeError(
            "Rust normalize_frame_sampling_strategy_token is unavailable."
        )
    return _normalize_frame_sampling_strategy_token(value)


def native_state_vocabularies() -> dict[str, frozenset[str]]:
    """Every native state, event and action vocabulary, keyed by vocabulary name."""
    if _native_state_vocabularies is None or not has_native_capability(
        "state_vocabulary", "state_vocabulary_v1"
    ):
        raise RuntimeError("required Rust state_vocabulary capability is unavailable")
    rows = _native_state_vocabularies()
    vocabularies = {name: frozenset(tokens) for name, tokens in rows}
    if len(vocabularies) != len(rows):
        raise RuntimeError("Rust returned duplicate state vocabulary names")
    return vocabularies


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
