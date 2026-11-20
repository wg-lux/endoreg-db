"""Kontrolliertes SHA-256-Experiment, kein Nachweis allgemeiner Kollisionsfreiheit.

Verlustfreies PNG verhindert, dass Kompression die Pixeländerung verwirft.
Jede Generation ändert genau einen Kanal eines Pixels um eine Intensitätsstufe.
Dateihashes messen Byteidentität und liefern keinen visuellen Schwellenwert.
"""

from collections.abc import Iterator
from io import BytesIO
from pathlib import Path
from uuid import uuid4

import pytest
from PIL import Image
from PIL.PngImagePlugin import PngInfo

from endoreg_db.utils.file_operations import (
    atomic_write_file,
    safe_unlink_file,
)
from endoreg_db.utils.hashs import get_file_hash
from endoreg_db.utils.paths import get_runtime_paths

pytestmark = pytest.mark.no_db


@pytest.fixture
def image_path() -> Iterator[Path]:
    path = get_runtime_paths().storage / f"pixel-experiment-{uuid4().hex}.png"
    try:
        yield path
    finally:
        safe_unlink_file(path, missing_ok=True)


def _png(pixels: bytes, side: int, metadata: PngInfo | None = None) -> bytes:
    with Image.frombytes("RGB", (side, side), pixels) as image:
        output = BytesIO()
        image.save(output, format="PNG", pnginfo=metadata)
        encoded = output.getvalue()
    with Image.open(BytesIO(encoded)) as decoded:
        assert decoded.tobytes() == pixels
    return encoded


@pytest.mark.parametrize("side", [8, 16])
@pytest.mark.parametrize("channel", [0, 1, 2], ids=["red", "green", "blue"])
def test_one_pixel_generations_and_restoration(
    image_path: Path, side: int, channel: int
) -> None:
    # Deterministic gradient, no patient data, random seeds or lossy encoding.
    original = bytes(index % 251 for index in range(side * side * 3))
    encoded = _png(original, side)
    atomic_write_file(destination=image_path, content=[encoded])
    original_hash = get_file_hash(image_path)
    copy_path = image_path.with_suffix(".copy.png")
    try:
        atomic_write_file(destination=copy_path, content=[image_path.read_bytes()])
        assert get_file_hash(copy_path) == original_hash
    finally:
        safe_unlink_file(copy_path, missing_ok=True)

    pixels = bytearray(original)
    hashes = [original_hash]
    for generation in range(1, side * side + 1):
        previous = bytes(pixels)
        pixels[(generation - 1) * 3 + channel] += 1
        assert sum(a != b for a, b in zip(previous, pixels, strict=True)) == 1
        assert sum(a != b for a, b in zip(original, pixels, strict=True)) == generation
        atomic_write_file(destination=image_path, content=[_png(bytes(pixels), side)])
        digest = get_file_hash(image_path)
        assert digest not in hashes, f"Hash repeated at generation {generation}"
        hashes.append(digest)

    # Reverse the same trajectory: earlier hashes recur only for restored bytes.
    for remaining in reversed(range(side * side)):
        pixels[remaining * 3 + channel] -= 1
        atomic_write_file(destination=image_path, content=[_png(bytes(pixels), side)])
        digest = get_file_hash(image_path)
        assert digest == hashes[remaining]
        assert (digest == original_hash) == (remaining == 0)
    assert image_path.read_bytes() == encoded


def test_identical_pixels_with_different_metadata_have_different_hashes(
    image_path: Path,
) -> None:
    pixels = bytes(range(192))
    original = _png(pixels, 8)
    atomic_write_file(destination=image_path, content=[original])
    original_hash = get_file_hash(image_path)
    metadata = PngInfo()
    metadata.add_text("experiment", "identical pixels, different metadata")
    tagged = _png(pixels, 8, metadata)
    assert tagged != original
    atomic_write_file(destination=image_path, content=[tagged])
    assert get_file_hash(image_path) != original_hash
