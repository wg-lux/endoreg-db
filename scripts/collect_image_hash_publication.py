"""Archive actual image-hash test inputs and observations without replacing hashing.

Run from the repository root with the project interpreter using ``-m scripts...``.
Output is a unique directory below get_runtime_paths().export_dir, plus a ZIP.
"""

from collections.abc import Callable, Iterator
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from importlib import import_module, metadata
from io import BytesIO, StringIO
from pathlib import Path
import csv
import platform
import subprocess
import sys
from typing import cast
from uuid import uuid4
from zipfile import ZIP_DEFLATED, ZipFile

import pytest
from PIL import Image
import yaml

from endoreg_db.utils.file_operations import atomic_write_file
from endoreg_db.utils.hashs import get_file_hash
from endoreg_db.utils.paths import get_runtime_paths


@dataclass(frozen=True)
class Observation:
    series: str
    step: int
    phase: str
    side: int
    changed_pixels: int
    changed_channels: int
    changed_pixels_previous: int
    max_channel_delta: int
    mse: float
    hash_distance_bits: int
    matches_original: bool
    sha256: str
    bytes: int
    image: str


def write(destination: Path, content: bytes) -> None:
    atomic_write_file(destination=destination, content=[content])


class Capture:
    def __init__(self, output: Path) -> None:
        self.output = output
        self.rows: list[Observation] = []

    @pytest.fixture(autouse=True)
    def observe(
        self, request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
    ) -> Iterator[None]:
        node: object = getattr(request, "node")
        if not isinstance(node, pytest.Function):
            raise TypeError("Capture requires a function-scoped pytest item")
        module = node.module
        series = node.name
        baseline: bytes | None = None
        baseline_hash = ""
        previous = b""
        step = 0

        def observed_hash(path: Path) -> str:
            nonlocal baseline, baseline_hash, previous, step
            digest = get_file_hash(path)
            encoded = path.read_bytes()
            with Image.open(BytesIO(encoded)) as image:
                assert image.mode == "RGB" and image.width == image.height
                side = image.width
                pixels = image.tobytes()
            if baseline is None:
                baseline, baseline_hash, previous = pixels, digest, pixels
            delta = [abs(a - b) for a, b in zip(baseline, pixels, strict=True)]
            changed = sum(any(delta[i : i + 3]) for i in range(0, len(delta), 3))
            previous_changed = sum(
                pixels[i : i + 3] != previous[i : i + 3]
                for i in range(0, len(pixels), 3)
            )
            if "metadata" in series:
                phase = "baseline" if step == 0 else "metadata"
            elif step < 2:
                phase = "baseline" if step == 0 else "copy"
            else:
                phase = "forward" if step < side * side + 2 else "reverse"
            relative = f"images/{series}/{step:04d}.png"
            archived = self.output / relative
            write(archived, encoded)
            assert get_file_hash(archived) == digest
            self.rows.append(
                Observation(
                    series,
                    step,
                    phase,
                    side,
                    changed,
                    sum(value != 0 for value in delta),
                    previous_changed,
                    max(delta),
                    sum(value * value for value in delta) / len(delta),
                    (int(digest, 16) ^ int(baseline_hash, 16)).bit_count(),
                    digest == baseline_hash,
                    digest,
                    len(encoded),
                    relative,
                )
            )
            previous = pixels
            step += 1
            return digest

        monkeypatch.setattr(module, "get_file_hash", observed_hash)
        yield


def export_tables(output: Path, rows: list[Observation]) -> None:
    buffer = StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=list(asdict(rows[0])))
    writer.writeheader()
    writer.writerows(asdict(row) for row in rows)
    write(output / "measurements.csv", buffer.getvalue().encode())
    summary: list[dict[str, str | int]] = []
    for series in dict.fromkeys(row.series for row in rows):
        selected = [row for row in rows if row.series == series]
        forward = [row for row in selected if row.phase == "forward"]
        summary.append(
            {
                "series": series,
                "side": selected[0].side,
                "observations": len(selected),
                "forward_generations": len(forward),
                "forward_original_matches": sum(
                    row.matches_original for row in forward
                ),
                "reverse_generations": sum(row.phase == "reverse" for row in selected),
                "copy_controls": sum(row.phase == "copy" for row in selected),
                "metadata_controls": sum(row.phase == "metadata" for row in selected),
            }
        )
    buffer = StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=list(summary[0]))
    writer.writeheader()
    writer.writerows(summary)
    write(output / "summary.csv", buffer.getvalue().encode())


def export_figures(output: Path, rows: list[Observation]) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    def save(name: str) -> None:
        # Narrow the external API: installed Matplotlib stubs leave IO and kwargs unknown.
        print_figure = cast(
            Callable[[BytesIO, float, str, str, str, str], object],
            getattr(fig.canvas, "print_figure"),
        )
        for extension in ("pdf", "svg", "png"):
            buffer = BytesIO()
            print_figure(buffer, 300, "white", "white", "portrait", extension)
            write(output / f"figures/{name}.{extension}", buffer.getvalue())
        plt.close()

    fig, axes = plt.subplots(2, 3, figsize=(10, 5), squeeze=False)
    for axis, series in zip(
        axes.flat,
        dict.fromkeys(row.series for row in rows if row.phase == "forward"),
        strict=True,
    ):
        selected = [
            row for row in rows if row.series == series and row.phase == "forward"
        ]
        axis.scatter(
            [row.changed_pixels for row in selected],
            [row.hash_distance_bits for row in selected],
            s=5,
        )
        axis.set(
            title=series.split("[")[1].rstrip("]"),
            ylim=(0, 256),
            xlabel="Geänderte Pixel gegenüber Original",
            ylabel="Hashdistanz (Bit)",
        )
    fig.tight_layout()
    save("hash_distance")

    fig, axes = plt.subplots(2, 3, figsize=(9, 6), squeeze=False)
    for row_index, side in enumerate((8, 16)):
        series = f"test_one_pixel_generations_and_restoration[red-{side}]"
        selected = [row for row in rows if row.series == series]
        original = next(row for row in selected if row.phase == "baseline")
        first = next(row for row in selected if row.phase == "forward")
        with Image.open(output / original.image) as image:
            axes[row_index, 0].imshow(image, interpolation="nearest")
        with Image.open(output / first.image) as image:
            axes[row_index, 1].imshow(image, interpolation="nearest")
        mask = [[0] * side for _ in range(side)]
        mask[0][0] = 1
        axes[row_index, 2].imshow(
            mask, cmap="gray", vmin=0, vmax=1, interpolation="nearest"
        )
        for column, title in enumerate(
            ("Original", "Ein Pixel: Rot +1", "Änderungsmaske (binär)")
        ):
            axes[row_index, column].set_title(f"{side} × {side}: {title}")
            axes[row_index, column].axis("off")
    fig.tight_layout()
    save("example_images")

    groups = [
        (phase, [row for row in rows if row.phase == phase])
        for phase in ("copy", "forward", "metadata")
    ]
    fig, axis = plt.subplots(figsize=(7, 4))
    scatter = cast(Callable[[list[int], list[float]], object], getattr(axis, "scatter"))
    scatter(
        [0, 1, 2],
        [
            sum(row.matches_original for row in group) / len(group)
            for _, group in groups
        ],
    )
    axis.set(
        ylabel="Anteil mit gleichem Ausgangshash",
        ylim=(-0.1, 1.1),
        xticks=[0, 1, 2],
        xticklabels=[
            f"{label}\n{sum(row.matches_original for row in group)}/{len(group)}"
            for label, (_, group) in zip(
                ("Bytekopie", "Pixeländerung", "Metadaten"), groups, strict=True
            )
        ],
    )
    fig.tight_layout()
    save("controls")


def main() -> int:
    # Resolve the durable export root before pytest installs its isolated runtime.
    started = datetime.now(timezone.utc)
    output = (
        get_runtime_paths().export_dir
        / f"image-hash-{started:%Y%m%dT%H%M%SZ}-{uuid4().hex[:8]}"
    )
    capture = Capture(output)
    log = StringIO()
    with redirect_stdout(log), redirect_stderr(log):
        result = pytest.main(
            ["tests/utils/test_image_content_hash.py", "-q", "--tb=short"],
            plugins=[capture],
        )
    write(output / "pytest.log", log.getvalue().encode())
    print(log.getvalue())
    if result != 0:
        raise RuntimeError(f"Experiment failed; log retained at {output}")
    rows = capture.rows
    assert len(rows) == 1934, f"Unexpected observation count: {len(rows)}"
    for phase, count in (
        ("baseline", 7),
        ("copy", 6),
        ("forward", 960),
        ("reverse", 960),
        ("metadata", 1),
    ):
        assert sum(row.phase == phase for row in rows) == count
    assert all(
        row.changed_pixels_previous == 1
        for row in rows
        if row.phase in ("forward", "reverse")
    )
    assert all(
        not row.matches_original for row in rows if row.phase in ("forward", "metadata")
    )
    export_tables(output, rows)
    export_figures(output, rows)
    sources = [
        "scripts/collect_image_hash_publication.py",
        "tests/utils/test_image_content_hash.py",
        "tests/conftest.py",
        "tests/plugins/runtime_paths.py",
        "tests/runtime_paths.py",
        "endoreg_db/utils/hashs.py",
        "endoreg_db/utils/rust_backend.py",
        "endoreg_db/utils/file_operations.py",
        "endoreg_db/utils/paths.py",
        "rust/endoreg_rust_backend/src/file_identity.rs",
        "rust/endoreg_rust_backend/Cargo.lock",
        "pytest.ini",
        "pyproject.toml",
        "uv.lock",
        "LICENSE",
    ]
    for source in sources:
        write(output / "code" / source, Path(source).read_bytes())
    write(output / "PROTOKOLL.md", Path("docs/image_hash_publication.md").read_bytes())
    native = import_module("endoreg_db.endoreg_rust_backend")
    native_file = native.__file__
    assert isinstance(native_file, str)
    environment = {
        "schema_version": 1,
        "started_utc": started.isoformat(),
        "finished_utc": datetime.now(timezone.utc).isoformat(),
        "command": ".devenv/state/venv/bin/python -m scripts.collect_image_hash_publication",
        "python": sys.version,
        "os": platform.system(),
        "os_release": platform.release(),
        "architecture": platform.machine(),
        "git_commit": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], text=True
        ).strip(),
        "dirty_worktree": bool(
            subprocess.check_output(["git", "status", "--porcelain"], text=True).strip()
        ),
        "native_backend_sha256": get_file_hash(Path(native_file)),
        "packages": dict(
            sorted(
                (dist.metadata["Name"], dist.version)
                for dist in metadata.distributions()
            )
        ),
        "observations": len(rows),
        "pytest_exit_code": int(result),
        "source_scope": "Selected exact source snapshots; full repository and native binary not bundled.",
    }
    write(
        output / "environment.yml",
        yaml.safe_dump(environment, sort_keys=False).encode(),
    )
    inventory = {
        str(path.relative_to(output)): get_file_hash(path)
        for path in sorted(output.rglob("*"))
        if path.is_file()
    }
    write(
        output / "checksums.yml",
        yaml.safe_dump(
            {"algorithm": "sha256", "excludes": ["checksums.yml"], "files": inventory}
        ).encode(),
    )
    archive = BytesIO()
    with ZipFile(archive, "w", compression=ZIP_DEFLATED) as bundle:
        for path in sorted(output.rglob("*")):
            if path.is_file():
                bundle.writestr(str(path.relative_to(output)), path.read_bytes())
    zip_path = output.with_suffix(".zip")
    write(zip_path, archive.getvalue())
    with ZipFile(zip_path) as bundle:
        assert bundle.testzip() is None, "ZIP integrity check failed"
        assert set(bundle.namelist()) == set(inventory) | {"checksums.yml"}
        for member in bundle.namelist():
            assert bundle.read(member) == (output / member).read_bytes(), member
    write(
        zip_path.with_suffix(".zip.sha256"),
        f"{get_file_hash(zip_path)}  {zip_path.name}\n".encode(),
    )
    print(f"Publication directory: {output}\nArchive: {zip_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
