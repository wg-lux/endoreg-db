# pyright: reportPrivateUsage=false, reportUnusedFunction=false
import logging
import os
import shutil
from functools import lru_cache
from pathlib import Path
from typing import Literal, Optional

from endoreg_db.config.env import get_ffmpeg_env_candidates

logger = logging.getLogger("ffmpeg_wrapper")


@lru_cache(maxsize=1)
def _resolve_ffmpeg_executable() -> Optional[str]:
    """Locate the ffmpeg executable using multiple discovery strategies."""
    # 1) Explicit overrides via env vars
    env_candidates = get_ffmpeg_env_candidates()

    # 2) Django settings overrides (if Django is configured)
    try:
        from django.conf import settings

        env_candidates.extend(
            getattr(settings, attr)
            for attr in ("FFMPEG_EXECUTABLE", "FFMPEG_BINARY", "FFMPEG_PATH")
            if hasattr(settings, attr)
        )
    except Exception:
        # Django might not be configured for every consumer
        pass

    # Normalize and verify explicit candidates
    for candidate in env_candidates:
        if not candidate:
            continue
        candidate_path = Path(candidate)
        if candidate_path.is_dir():
            candidate_path = candidate_path / "ffmpeg"
        if candidate_path.exists() and os.access(candidate_path, os.X_OK):
            logger.debug("Using ffmpeg executable override at %s", candidate_path)
            return str(candidate_path)

    # 3) PATH lookup (shutil.which)
    via_path = shutil.which("ffmpeg")
    if via_path:
        return via_path

    # 4) Common fallback locations (useful for Nix-based environments)
    nix_store = Path("/nix/store")
    if nix_store.exists():
        patterns = (
            "*-ffmpeg-*/bin/ffmpeg",
            "*-ffmpeg-headless-*/bin/ffmpeg",
            "*-ffmpeg-headless*/bin/ffmpeg",
        )
        for pattern in patterns:
            matches = sorted(nix_store.glob(pattern))
            if matches:
                logger.debug("Discovered ffmpeg in nix store at %s", matches[-1])
                return str(matches[-1])

    # 5) Final fallback to standard Unix locations
    for fallback in (Path("/usr/bin/ffmpeg"), Path("/usr/local/bin/ffmpeg")):
        if fallback.exists() and os.access(fallback, os.X_OK):
            return str(fallback)

    return None


def resolve_ffmpeg_executable() -> Optional[str]:
    return _resolve_ffmpeg_executable()


@lru_cache(maxsize=1)
def _resolve_ffprobe_executable() -> Optional[str]:
    """Locate ffprobe, preferring the same directory as the selected ffmpeg."""
    ffmpeg_executable = _resolve_ffmpeg_executable()
    if ffmpeg_executable:
        ffprobe_sibling = Path(ffmpeg_executable).with_name("ffprobe")
        if ffprobe_sibling.exists() and os.access(ffprobe_sibling, os.X_OK):
            return str(ffprobe_sibling)

    via_path = shutil.which("ffprobe")
    if via_path:
        return via_path

    for fallback in (Path("/usr/bin/ffprobe"), Path("/usr/local/bin/ffprobe")):
        if fallback.exists() and os.access(fallback, os.X_OK):
            return str(fallback)

    return None


def resolve_ffprobe_executable() -> Optional[str]:
    return _resolve_ffprobe_executable()


def is_ffmpeg_available() -> bool:
    """
    Checks whether the FFmpeg executable is available in the system's PATH.

    Returns:
        True if FFmpeg is found in the PATH; otherwise, False.
    """
    return _resolve_ffmpeg_executable() is not None


def check_ffmpeg_availability() -> Literal[True]:
    """
    Verifies that FFmpeg is installed and available in the system's PATH.

    Raises:
        FileNotFoundError: If FFmpeg is not found.

    Returns:
        True if FFmpeg is available.
    """
    if not is_ffmpeg_available():
        error_msg = (
            "FFmpeg is not available. Please install it and ensure it's in your PATH."
        )
        logger.error(error_msg)
        raise FileNotFoundError(error_msg)
    # logger.info("FFmpeg is available.") # Caller can log if needed
    return True
