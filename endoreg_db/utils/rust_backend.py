from __future__ import annotations

import logging
from importlib import import_module
from pathlib import Path
from typing import Callable, Sequence

logger = logging.getLogger(__name__)

_parse_extracted_frame_numbers: Callable[[list[str]], list[int]] | None
_build_expected_frame_records: Callable[[int, str], list[tuple[int, str]]] | None
_build_frame_records: Callable[..., list[tuple[int, str]]] | None
_render_single_page_pdf: Callable[[str], bytes] | None
_sha256_file_hex: Callable[[Path, int], str] | None
_encryption_status: Callable[[Path], str] | None
_is_lx_encrypted_file: Callable[[Path], bool] | None
_copy_file_descriptor_to_path: Callable[[int, Path, int], int] | None
_derive_anonymization_status: (
    Callable[[bool, bool, bool, bool, bool, bool, bool], str] | None
)
_derive_report_anonymization_status: (
    Callable[[bool, bool, bool, bool, bool], str] | None
)
_derive_segment_annotation_status: Callable[[bool, bool, bool], str] | None
_derive_frame_annotation_status: Callable[[bool, bool, bool, bool, bool], str] | None
_normalize_frame_task_mode_token: Callable[[str], str] | None
_normalize_frame_sampling_strategy_token: Callable[[str], str] | None
_storage_profile_policy_rows: Callable[[], list[tuple[str, str, str]]] | None
_rust_backend_available = False

try:
    rust_backend = import_module("endoreg_db.endoreg_rust_backend")
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
    _encryption_status = getattr(rust_backend, "encryption_status", None)
    _is_lx_encrypted_file = getattr(rust_backend, "is_lx_encrypted_file", None)
    _copy_file_descriptor_to_path = getattr(
        rust_backend, "copy_file_descriptor_to_path", None
    )
    _storage_profile_policy_rows = getattr(
        rust_backend, "storage_profile_policy_rows", None
    )

    _rust_backend_available = True
except Exception as exc:
    logger.debug("Rust backend unavailable, using Python fallbacks: %s", exc)
    _build_expected_frame_records = None
    _build_frame_records = None
    _parse_extracted_frame_numbers = None
    _render_single_page_pdf = None
    _sha256_file_hex = None
    _encryption_status = None
    _is_lx_encrypted_file = None
    _copy_file_descriptor_to_path = None
    _derive_anonymization_status = None
    _derive_report_anonymization_status = None
    _derive_segment_annotation_status = None
    _derive_frame_annotation_status = None
    _normalize_frame_task_mode_token = None
    _normalize_frame_sampling_strategy_token = None
    _storage_profile_policy_rows = None
    _rust_backend_available = False

RUST_BACKEND_AVAILABLE: bool = _rust_backend_available


def sha256_file_hex(path: Path, chunk_size: int) -> str | None:
    if _sha256_file_hex is None:
        return None
    try:
        return _sha256_file_hex(Path(path), chunk_size)
    except Exception as exc:
        logger.warning("Rust sha256_file_hex failed, falling back to Python: %s", exc)
        return None


def encryption_status(path: Path) -> str | None:
    if _encryption_status is None:
        return None
    try:
        status = _encryption_status(Path(path))
    except Exception as exc:
        logger.warning("Rust encryption_status failed, falling back to Python: %s", exc)
        return None
    return status if status in {"encrypted", "plaintext"} else None


def is_lx_encrypted_file(path: Path) -> bool | None:
    if _is_lx_encrypted_file is None:
        return None
    try:
        return bool(_is_lx_encrypted_file(Path(path)))
    except Exception as exc:
        logger.warning(
            "Rust is_lx_encrypted_file failed, falling back to Python: %s", exc
        )
        return None


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
    except Exception as exc:
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
    except Exception as exc:
        logger.warning(
            "Rust render_single_page_pdf failed, falling back to Python: %s", exc
        )
        return None


def parse_extracted_frame_numbers(paths: Sequence[Path]) -> list[int] | None:
    if _parse_extracted_frame_numbers is None:
        return None
    try:
        return list(_parse_extracted_frame_numbers([str(path) for path in paths]))
    except Exception as exc:
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
    except Exception as exc:
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
    except Exception as exc:
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
    except Exception as exc:
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
    except Exception as exc:
        logger.warning(
            "Rust derive_report_anonymization_status failed, falling back to Python: %s",
            exc,
        )
        return None


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
    except Exception as exc:
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
    except Exception as exc:
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
    except Exception as exc:
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
    except Exception as exc:
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
    except Exception as exc:
        logger.warning(
            "Rust storage_profile_policy_rows failed; storage routing cannot use Rust table: %s",
            exc,
        )
        return None
