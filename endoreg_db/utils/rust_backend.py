from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable, Sequence

logger = logging.getLogger(__name__)

_parse_extracted_frame_numbers: Callable[[list[str]], list[int]] | None
_build_expected_frame_records: Callable[[int, str], list[tuple[int, str]]] | None
_build_frame_records: Callable[..., list[tuple[int, str]]] | None
_render_single_page_pdf: Callable[[str], bytes] | None
_sha256_file_hex: Callable[[Path, int], str] | None
_rust_backend_available = False

try:
    from endoreg_db.endoreg_rust_backend import (
        build_expected_frame_records as _build_expected_frame_records,
    )
    from endoreg_db.endoreg_rust_backend import (
        build_frame_records as _build_frame_records,
    )
    from endoreg_db.endoreg_rust_backend import (
        parse_extracted_frame_numbers as _parse_extracted_frame_numbers,
    )
    from endoreg_db.endoreg_rust_backend import (
        render_single_page_pdf as _render_single_page_pdf,
    )
    from endoreg_db.endoreg_rust_backend import sha256_file_hex as _sha256_file_hex

    _rust_backend_available = True
except Exception as exc:
    logger.debug("Rust backend unavailable, using Python fallbacks: %s", exc)
    _build_expected_frame_records = None
    _build_frame_records = None
    _parse_extracted_frame_numbers = None
    _render_single_page_pdf = None
    _sha256_file_hex = None
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
