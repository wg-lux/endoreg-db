from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable, Sequence

logger = logging.getLogger(__name__)

_parse_extracted_frame_numbers: Callable[[list[str]], list[int]] | None
_render_single_page_pdf: Callable[[str], bytes] | None
_sha256_file_hex: Callable[[Path, int], str] | None

try:
    from endoreg_rust_backend import (
        parse_extracted_frame_numbers as _parse_extracted_frame_numbers,
    )
    from endoreg_rust_backend import render_single_page_pdf as _render_single_page_pdf
    from endoreg_rust_backend import sha256_file_hex as _sha256_file_hex

    RUST_BACKEND_AVAILABLE = True
except Exception as exc:
    logger.debug("Rust backend unavailable, using Python fallbacks: %s", exc)
    _parse_extracted_frame_numbers = None
    _render_single_page_pdf = None
    _sha256_file_hex = None
    RUST_BACKEND_AVAILABLE = False


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
