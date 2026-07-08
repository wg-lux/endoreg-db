# pyright: reportPrivateUsage=false, reportUnusedFunction=false
import logging
import subprocess
from typing import Dict, List, Optional, Tuple

from .executable_discovery import _resolve_ffmpeg_executable

logger = logging.getLogger("ffmpeg_wrapper")

# Global hardware acceleration cache
_nvenc_available = None
_preferred_encoder = None


def _detect_nvenc_support() -> bool:
    """
    Detect if NVIDIA NVENC hardware acceleration is available.

    Returns:
        True if NVENC is available, False otherwise
    """
    ffmpeg_executable = _resolve_ffmpeg_executable()
    if not ffmpeg_executable:
        logger.debug("NVENC detection skipped because ffmpeg is unavailable")
        return False

    try:
        # Test NVENC availability with a minimal command (minimum size for NVENC)
        cmd = [
            ffmpeg_executable,
            "-nostdin",
            "-hide_banner",
            "-f",
            "lavfi",
            "-i",
            "testsrc=duration=1:size=256x256:rate=1",
            "-c:v",
            "h264_nvenc",
            "-preset",
            "p1",
            "-f",
            "null",
            "-",
        ]

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
            stdin=subprocess.DEVNULL,
        )

        if result.returncode == 0:
            logger.debug("NVENC h264 encoding test successful")
            return True
        else:
            logger.debug(f"NVENC test failed: {result.stderr}")
            return False

    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        logger.debug(f"NVENC detection failed: {e}")
        return False
    except Exception as e:
        logger.warning(f"Unexpected error during NVENC detection: {e}")
        return False


def _get_preferred_encoder() -> Dict[str, str]:
    """
    Get the preferred video encoder configuration based on available hardware.

    Returns:
        Dictionary with encoder configuration
    """
    global _nvenc_available, _preferred_encoder

    if _nvenc_available is None:
        _nvenc_available = _detect_nvenc_support()

    if _preferred_encoder is None:
        if _nvenc_available:
            _preferred_encoder = {
                "name": "h264_nvenc",
                "preset_param": "-preset",
                "preset_value": "p4",  # Medium quality/speed for NVENC
                "quality_param": "-cq",
                "quality_value": "20",  # NVENC CQ mode
                "type": "nvenc",
                "fallback_preset": "p1",  # Fastest NVENC preset for fallback
            }
            logger.info("Hardware acceleration: NVENC available")
        else:
            _preferred_encoder = {
                "name": "libx264",
                "preset_param": "-preset",
                "preset_value": "medium",  # CPU preset
                "quality_param": "-crf",
                "quality_value": "23",  # CPU CRF mode
                "type": "cpu",
                "fallback_preset": "ultrafast",  # Fastest CPU preset for fallback
            }
            logger.info("Hardware acceleration: NVENC not available, using CPU")

    return _preferred_encoder


def _get_encoder_config(encoder_type: str) -> Dict[str, str]:
    if encoder_type == "nvenc":
        return {
            "name": "h264_nvenc",
            "preset_param": "-preset",
            "preset_value": "p4",
            "quality_param": "-cq",
            "quality_value": "20",
            "type": "nvenc",
            "fallback_preset": "p1",
        }
    return {
        "name": "libx264",
        "preset_param": "-preset",
        "preset_value": "medium",
        "quality_param": "-crf",
        "quality_value": "23",
        "type": "cpu",
        "fallback_preset": "ultrafast",
    }


def _build_encoder_args(
    quality_mode: str = "balanced",
    fallback: bool = False,
    custom_crf: Optional[int] = None,
    encoder_type_override: Optional[str] = None,
) -> Tuple[List[str], str]:
    """
    Build encoder command arguments based on available hardware and quality requirements.

    Args:
        quality_mode: 'fast', 'balanced', or 'quality'
        fallback: Whether to use fallback settings for compatibility
        custom_crf: Override quality setting (for backward compatibility)

    Returns:
        Tuple of (encoder_args, encoder_type)
    """
    if encoder_type_override is None:
        encoder = _get_preferred_encoder()
        effective_encoder_type = encoder["type"]
    else:
        encoder = _get_encoder_config(encoder_type_override)
        effective_encoder_type = encoder_type_override

    if effective_encoder_type == "nvenc":
        # NVIDIA NVENC configuration
        if fallback:
            preset = encoder["fallback_preset"]  # p1 - fastest
            quality = "28"  # Lower quality for speed
        elif quality_mode == "fast":
            preset = "p2"  # Faster preset
            quality = "25"
        elif quality_mode == "quality":
            preset = "p6"  # Higher quality preset
            quality = "18"
        else:  # balanced
            preset = encoder["preset_value"]  # p4
            quality = encoder["quality_value"]  # 20

        # Override with custom CRF if provided (for backward compatibility)
        if custom_crf is not None:
            quality = str(custom_crf)

        return [
            "-c",
            encoder["name"],
            encoder["preset_param"],
            preset,
            encoder["quality_param"],
            quality,
            "-gpu",
            "0",  # Use first GPU
            "-rc",
            "vbr",  # Variable bitrate
            "-vf",
            "format=yuv420p",
            "-profile:v",
            "high",
        ], "nvenc"
    else:
        # CPU libx264 configuration
        if fallback:
            preset = "ultrafast"
            quality = "28"  # Lower quality for speed
        elif quality_mode == "fast":
            preset = "faster"
            quality = "20"
        elif quality_mode == "quality":
            preset = "slow"
            quality = "18"
        else:  # balanced
            preset = "medium"
            quality = "23"

        # Override with custom CRF if provided (for backward compatibility)
        if custom_crf is not None:
            quality = str(custom_crf)

        return [
            "-c:v",
            "libx264",
            "-preset",
            preset,
            "-crf",
            quality,
            "-profile:v",
            "high",
        ], "cpu"
