"""Synthetic known-key candidate evaluation using the production hashing helpers.

Run with the project interpreter: python -m scripts.evaluate_patient_hmac.
No database, patient records, configured key files or production settings are read.
"""

from dataclasses import asdict, dataclass
from collections.abc import Callable
from datetime import date, timedelta
from io import BytesIO, StringIO
import csv
import json
import platform
from pathlib import Path
from statistics import median
from time import perf_counter
from typing import cast
from uuid import uuid4

KEY_A = "1b8d6c2e7a9f04356d8c1e7b2a4f60939c5d8a0e2f7b14366a9c8e1d5b307f42"
KEY_B = "a9c43f07d1e286b58f9062ac743de1b0c6258fda9347e02b165dc8907eaf3b42"
TARGET_DATE = date(1980, 7, 14)
SCENARIOS = (
    ("shared_source_context", "Shared key / A context", KEY_A, "centre-a", 1),
    ("shared_other_context", "Shared key / B context", KEY_A, "centre-b", 0),
    ("local_wrong_key", "Local B key / A context", KEY_B, "centre-a", 0),
    ("local_other_fingerprint", "Local B key / B context", KEY_B, "centre-b", 0),
    (
        "local_source_compromised",
        "Local A key disclosed / A context",
        KEY_A,
        "centre-a",
        1,
    ),
)


@dataclass(frozen=True)
class Observation:
    scenario: str
    repeat: int
    attempts: int
    matches: int
    first_match_attempt: int | None
    recovered_date: str | None
    elapsed_seconds: float


def evaluate(repeats: int = 10) -> list[Observation]:
    """Completely enumerate the fixed 366-date domain, including after a match."""
    if repeats < 1:
        raise ValueError("repeats must be positive")
    from endoreg_db.config.identity_hashing import identity_keyring_snapshot
    from endoreg_db.utils.hashs import get_patient_identity_fingerprint

    token = identity_keyring_snapshot.set(None)
    try:
        target = get_patient_identity_fingerprint(
            "Synthetic", "Person", TARGET_DATE, "centre-a", salt=KEY_A
        )
        observations: list[Observation] = []
        for repeat in range(repeats):
            # Rotate order to reduce a fixed scenario-order timing bias.
            offset = repeat % len(SCENARIOS)
            for name, _, key, centre, expected in (
                SCENARIOS[offset:] + SCENARIOS[:offset]
            ):
                start = perf_counter()
                matches: list[tuple[int, date]] = []
                for index in range(366):
                    candidate = date(1980, 1, 1) + timedelta(days=index)
                    digest = get_patient_identity_fingerprint(
                        "Synthetic", "Person", candidate, centre, salt=key
                    )
                    if digest == target:
                        matches.append((index + 1, candidate))
                elapsed = perf_counter() - start
                assert len(matches) == expected
                assert not matches or matches[0][1] == TARGET_DATE
                observations.append(
                    Observation(
                        name,
                        repeat,
                        366,
                        len(matches),
                        matches[0][0] if matches else None,
                        matches[0][1].isoformat() if matches else None,
                        elapsed,
                    )
                )
        return observations
    finally:
        identity_keyring_snapshot.reset(token)


def main() -> None:
    from django.conf import settings

    if settings.configured:
        raise RuntimeError("Run this synthetic evaluation in a fresh process")
    settings.configure(SECRET_KEY="synthetic-evaluation-only")
    # Import application helpers only after isolating Django configuration.
    from endoreg_db.utils.file_operations import atomic_write_file
    from endoreg_db.utils.hashs import get_file_hash
    from endoreg_db.utils.paths import get_runtime_paths
    from endoreg_db.utils import hashs

    output = get_runtime_paths().export_dir / f"patient-hmac-{uuid4().hex[:12]}"
    rows = evaluate()
    table = StringIO()
    writer = csv.DictWriter(table, fieldnames=list(asdict(rows[0])))
    writer.writeheader()
    writer.writerows(asdict(row) for row in rows)
    atomic_write_file(
        destination=output / "measurements.csv", content=[table.getvalue().encode()]
    )
    metadata = {
        "python": platform.python_version(),
        "platform": platform.platform(),
        "processor": platform.processor(),
        "hash_helper_sha256": get_file_hash(Path(hashs.__file__)),
        "script_sha256": get_file_hash(Path(__file__)),
        "repeats_per_scenario": 10,
        "candidate_dates_per_scan": 366,
        "total_candidate_evaluations": sum(row.attempts for row in rows),
        "key_material": "Public synthetic 64-character strings passed unchanged as UTF-8",
        "timing_scope": "Complete helper loop, including encoding and key validation; no setup or plotting",
        "limitations": "One host; repeated synthetic tuple; concurrent workloads uncontrolled; not 2PC",
        "scenarios": [
            {
                "name": name,
                "label": label,
                "median_ms": median(
                    r.elapsed_seconds * 1000 for r in rows if r.scenario == name
                ),
                "min_ms": min(
                    r.elapsed_seconds * 1000 for r in rows if r.scenario == name
                ),
                "max_ms": max(
                    r.elapsed_seconds * 1000 for r in rows if r.scenario == name
                ),
            }
            for name, label, _, _, _ in SCENARIOS
        ],
    }
    atomic_write_file(
        destination=output / "environment.json",
        content=[json.dumps(metadata, indent=2).encode()],
    )
    export_figures(output, rows)
    print(output)


def export_figures(output: Path, rows: list[Observation]) -> None:
    """Render observed outcomes separately from scan costs; reusable on archived rows."""
    from endoreg_db.utils.file_operations import atomic_write_file
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.collections import LineCollection
    from matplotlib.container import BarContainer
    from matplotlib.patches import Rectangle
    from matplotlib.text import Text

    labels = [
        "Shared key, A context",
        "Shared key, B context",
        "B's separate key, A context",
        "B's separate key, B context",
        "A's disclosed key, A context",
    ]
    for kind in ("candidate_matches", "scan_times"):
        figure, axes = plt.subplots(figsize=(10, 4.6), layout="constrained")
        text = cast(Callable[[float, float, str], Text], getattr(axes, "text"))
        if kind == "candidate_matches":
            axes.set(xlim=(0, 10), ylim=(-0.6, 6.4))
            axes.axis("off")
            text(0, 6, "Observed matches against A's target fingerprint").set_fontsize(
                15
            )
            for x, label in (
                (0, "Key and centre used"),
                (5.3, "Matches / scan"),
                (7.4, "Date recovered"),
            ):
                text(x, 5.2, label).set_fontweight("bold")
            for index, (name, _, _, _, _) in enumerate(SCENARIOS):
                group = [r for r in rows if r.scenario == name]
                outcomes = {(r.matches, r.recovered_date) for r in group}
                if len(outcomes) != 1:
                    raise ValueError(
                        "Outcome table requires identical outcomes across repetitions"
                    )
                row = group[0]
                y = 4.3 - index
                axes.add_patch(
                    Rectangle(
                        (0, y - 0.2),
                        10,
                        0.8,
                        facecolor="#e6f3f3" if row.matches else "#f1f3f5",
                        edgecolor="none",
                    )
                )
                text(0.12, y, labels[index])
                text(5.5, y, f"{row.matches} / {row.attempts}").set_fontweight("bold")
                text(7.5, y, row.recovered_date or "None")
            text(
                0,
                -0.5,
                "Same outcome in all 10 repetitions; each scan tested every one of 366 dates.",
            ).set_fontsize(10)
        else:
            barh = cast(
                Callable[[list[int], list[float]], BarContainer], getattr(axes, "barh")
            )
            hlines = cast(
                Callable[[int, float, float], LineCollection], getattr(axes, "hlines")
            )
            for index, (name, _, _, _, _) in enumerate(SCENARIOS):
                times = [r.elapsed_seconds * 1000 for r in rows if r.scenario == name]
                bars = barh([index], [median(times)])
                bars.patches[0].set_facecolor("#a6b5c3")
                hlines(index, min(times), max(times)).set_color("#25374a")
                text(max(times) + 0.12, index + 0.08, f"{median(times):.2f} ms")
            axes.set(
                yticks=list(range(len(SCENARIOS))),
                yticklabels=labels,
                xlim=(0, max(r.elapsed_seconds * 1000 for r in rows) * 1.24),
                xlabel="Complete scan time (ms), regardless of match outcome",
                title="Computation cost: bars = median; lines = observed min–max (10 scans)",
            )
            axes.invert_yaxis()
            axes.spines[["top", "right"]].set_visible(False)
        for extension in ("png", "svg", "pdf"):
            buffer = BytesIO()
            print_figure = cast(
                Callable[[BytesIO, float, str, str, str, str], object],
                getattr(figure.canvas, "print_figure"),
            )
            print_figure(buffer, 220, "white", "white", "portrait", extension)
            atomic_write_file(
                destination=output / f"{kind}.{extension}", content=[buffer.getvalue()]
            )
        plt.close(figure)


if __name__ == "__main__":
    main()
