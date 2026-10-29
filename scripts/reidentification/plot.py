"""Render public measurements only; never rerun attacks or read private truth."""

from __future__ import annotations

import argparse
from collections import defaultdict
from collections.abc import Callable
from typing import cast
from io import BytesIO
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.axes import Axes
from matplotlib.container import BarContainer
from matplotlib.text import Text
from matplotlib.figure import Figure
from matplotlib.backends.backend_pdf import PdfPages
from pydantic import TypeAdapter

from scripts.reidentification.run import Score, initialize

TEAL = "#087f8c"
RED = "#c44938"
GOLD = "#cf951f"
GREY = "#aeb9c5"
INK = "#20354b"
NOTE = "Drei synthetische Versuche · Summen über Versuchspaare · Keine klinische Erfolgswahrscheinlichkeit"
LABELS = {
    "A": "A · Namen/Jahr unbekannt",
    "A_unknown_centre": "A · Zentrum ebenfalls unbekannt",
    "B_shared": "B · Gemeinsamer Schlüssel",
    "B_independent": "B · Nur Zentrum a kompromittiert",
    "B_retained_exposed": "B · Historischer Schlüssel",
    "B_wrong_key": "B · Falscher Schlüssel",
    "B_shared_budget32": "B · Lokal, 32 Auswertungen",
    "B_oracle": "B · Oracle, 32 Anfragen",
    "C_independent_names_narrow_dates": "C · Engerer unabhängiger Prior",
    "D_labelled": "D · Mappings bereits bekannt",
    "examination_independent_times": "Untersuchung · Zeitkandidaten",
    "examination_patient_tokens_exposed": "Untersuchung · Patienten-Token bekannt",
    "examiner": "Untersucher · Personalliste",
}


def decorate(figure: Figure, title: str, subtitle: str) -> None:
    cast(Callable[..., Text], getattr(figure, "suptitle"))(
        title, x=0.04, y=0.97, ha="left", fontsize=21, fontweight="bold", color=INK
    )
    cast(Callable[..., Text], getattr(figure, "text"))(
        0.04, 0.91, subtitle, fontsize=11, color=INK
    )
    cast(Callable[..., Text], getattr(figure, "text"))(
        0.04, 0.025, NOTE, fontsize=9, color="#526478"
    )


def style_axis(axis: Axes, names: list[str]) -> None:
    cast(Callable[..., object], getattr(axis, "set_yticks"))(
        range(len(names)), [LABELS[name] for name in names]
    )
    axis.invert_yaxis()
    axis.spines[["top", "right", "left"]].set_visible(False)
    cast(Callable[..., None], getattr(axis, "tick_params"))(axis="y", length=0, pad=10)
    axis.set_axisbelow(True)
    cast(Callable[..., None], getattr(axis, "grid"))(axis="x", alpha=0.16)


def build(rows: list[Score]) -> dict[str, Figure]:
    groups: dict[str, list[Score]] = defaultdict(list)
    for row in rows:
        groups[row.scenario].append(row)
    if any(len(group) != 3 for group in groups.values()):
        raise ValueError("Figures require three recorded trials per scenario")
    for row in rows:
        if sum(row.coverage.values()) != row.eligible_rows:
            raise ValueError("Coverage categories must partition exposed rows")
    plt.rcParams.update(
        {
            "font.family": "DejaVu Sans",
            "font.size": 11,
            "axes.labelcolor": INK,
            "text.color": INK,
            "figure.facecolor": "white",
            "savefig.facecolor": "white",
        }
    )
    figures: dict[str, Figure] = {}

    names = [
        "A",
        "B_shared",
        "B_independent",
        "B_wrong_key",
        "B_shared_budget32",
        "B_oracle",
        "C_independent_names_narrow_dates",
        "D_labelled",
        "examination_independent_times",
    ]
    figure, axis = plt.subplots(figsize=(14, 8.5))
    decorate(
        figure,
        "Was wurde zusätzlich über Personen gelernt?",
        "Neue korrekte Zeilen-Person-Mappings gegenüber der Baseline; 432 exponierte Zeilen je Bedingung",
    )
    for offset, field, color, label in (
        (-0.18, "new_correct_associations", TEAL, "Neu und korrekt"),
        (0.18, "false_associations", RED, "Falsche Zuordnung"),
    ):
        values = [sum(getattr(r, field) for r in groups[name]) for name in names]
        bars = cast(Callable[..., BarContainer], getattr(axis, "barh"))(
            [i + offset for i in range(len(names))],
            values,
            height=0.32,
            color=color,
            label=label,
        )
        cast(Callable[..., object], getattr(axis, "bar_label"))(
            bars, padding=5, fontsize=10
        )
    style_axis(axis, names)
    axis.set_xlim(0, 170)
    cast(Callable[..., Text], getattr(axis, "set_xlabel"))(
        "Zeilen-Person-Zuordnungen, drei Versuche zusammen"
    )
    cast(Callable[..., object], getattr(axis, "legend"))(
        loc="lower right", frameon=False
    )
    cast(Callable[..., Text], getattr(figure, "text"))(
        0.35,
        0.085,
        "A: 15 kodierte Tupel, keine Personenbehauptung.  D: 432 Mappings schon vorher bekannt.",
        fontsize=10,
    )
    figure.subplots_adjust(left=0.35, right=0.95, top=0.85, bottom=0.16)
    figures["01_incremental_associations"] = figure

    names = [
        "B_shared",
        "B_independent",
        "B_shared_budget32",
        "B_oracle",
        "B_wrong_key",
        "examination_independent_times",
    ]
    figure, axis = plt.subplots(figsize=(14, 7.5))
    decorate(
        figure,
        "Mitgliedschaft: Treffer, Fehlattribution oder unbekannt?",
        "Je Bedingung 144 Hilfskandidaten-Versuchspaare; dieselben Personen können in mehreren Versuchen vorkommen",
    )
    left = [0] * len(names)
    for field, color, label in (
        ("membership_tp", TEAL, "Korrekt enthalten"),
        ("membership_fp", RED, "Fälschlich enthalten"),
        ("membership_ambiguous", GOLD, "Mehrdeutig"),
        ("membership_unknown", GREY, "Unbekannt"),
    ):
        values = [sum(getattr(r, field) for r in groups[name]) for name in names]
        cast(Callable[..., BarContainer], getattr(axis, "barh"))(
            range(len(names)), values, left=left, color=color, height=0.6, label=label
        )
        for i, value in enumerate(values):
            if value:
                cast(Callable[..., Text], getattr(axis, "text"))(
                    left[i] + value / 2,
                    i,
                    str(value),
                    ha="center",
                    va="center",
                    fontsize=9,
                    color="white" if color in (TEAL, RED) else INK,
                )
        left = [a + b for a, b in zip(left, values, strict=True)]
    if left != [144] * len(names):
        raise ValueError("Unexpected membership denominator")
    style_axis(axis, names)
    axis.set_xlim(0, 144)
    cast(Callable[..., Text], getattr(axis, "set_xlabel"))("Kandidaten-Versuchspaare")
    cast(Callable[..., object], getattr(axis, "legend"))(
        loc="upper center", bbox_to_anchor=(0.4, -0.15), ncol=2, frameon=False
    )
    cast(Callable[..., Text], getattr(figure, "text"))(
        0.04,
        0.075,
        "Keine Abwesenheitsbehauptungen. Unbekannt bedeutet nicht abwesend; gleiche Demografie beweist keine eindeutige Person.",
        fontsize=10,
    )
    figure.subplots_adjust(left=0.35, right=0.96, top=0.84, bottom=0.27)
    figures["02_membership"] = figure

    names = [
        "A",
        "A_unknown_centre",
        "B_shared",
        "B_independent",
        "C_independent_names_narrow_dates",
        "examination_independent_times",
    ]
    figure, axis = plt.subplots(figsize=(14, 8))
    decorate(
        figure,
        "Warum bleiben Zielzeilen unerkannt?",
        "Privates Scoring nach dem Angriff: 432 Zielzeilen je Bedingung, ohne Rückmeldung an den Angreifer",
    )
    left = [0] * len(names)
    for field, color, label in (
        ("reached_matched", TEAL, "Erreicht und Digest passend"),
        ("inside_beyond_prefix", "#8dcbd0", "Im Kandidatenraum, noch nicht erreicht"),
        ("outside_universe", GOLD, "Außerhalb des Kandidatenraums"),
        ("wrong_key_context_or_scheme", GREY, "Schlüssel / Kontext nicht verfügbar"),
        ("reached_no_match", RED, "Erreicht, aber kein Treffer"),
    ):
        values = [sum(r.coverage[field] for r in groups[name]) for name in names]
        if not any(values):
            continue
        cast(Callable[..., BarContainer], getattr(axis, "barh"))(
            range(len(names)), values, left=left, color=color, height=0.6, label=label
        )
        for i, value in enumerate(values):
            if value:
                cast(Callable[..., Text], getattr(axis, "text"))(
                    left[i] + value / 2,
                    i,
                    str(value),
                    ha="center",
                    va="center",
                    fontsize=10,
                    color="white" if color == TEAL else INK,
                )
        left = [a + b for a, b in zip(left, values, strict=True)]
    style_axis(axis, names)
    axis.set_xlim(0, 432)
    cast(Callable[..., Text], getattr(axis, "set_xlabel"))(
        "Zielzeilen, drei Versuche zusammen"
    )
    cast(Callable[..., object], getattr(axis, "legend"))(
        loc="upper center",
        bbox_to_anchor=(0.4, -0.14),
        ncol=2,
        frameon=False,
        fontsize=10,
    )
    cast(Callable[..., Text], getattr(figure, "text"))(
        0.04,
        0.075,
        "Digesttreffer sind keine eindeutigen Personenidentifikationen. Erreichte kompatible Nichttreffer: 0.",
        fontsize=10,
    )
    figure.subplots_adjust(left=0.35, right=0.96, top=0.84, bottom=0.26)
    figures["03_coverage"] = figure

    names = [
        "A",
        "A_unknown_centre",
        "C_independent_names_narrow_dates",
        "B_shared",
        "B_shared_budget32",
        "B_oracle",
        "examination_independent_times",
        "examination_patient_tokens_exposed",
    ]
    figure = cast(Callable[..., Figure], getattr(plt, "figure"))(figsize=(16, 9))
    axis = cast(Callable[..., Axes], getattr(figure, "add_axes"))(
        (0.32, 0.22, 0.27, 0.62)
    )
    timing = cast(Callable[..., Axes], getattr(figure, "add_axes"))(
        (0.69, 0.22, 0.25, 0.62)
    )
    decorate(
        figure,
        "Wie viel Arbeit wurde tatsächlich ausgeführt?",
        "Pro Versuch; logarithmische Achsen. Zeitpunkte zeigen alle drei Messungen, keine Erfolgswahrscheinlichkeit.",
    )
    values = [groups[name][0].evaluations for name in names]
    cast(Callable[..., object], getattr(axis, "scatter"))(
        values, range(len(names)), s=65, color=TEAL
    )
    for i, value in enumerate(values):
        cast(Callable[..., Text], getattr(axis, "annotate"))(
            f"{value:,}".replace(",", "."),
            (value, i),
            xytext=(7, 0),
            textcoords="offset points",
            va="center",
            fontsize=10,
        )
    style_axis(axis, names)
    cast(Callable[..., None], getattr(axis, "set_xscale"))("log")
    axis.set_xlim(15, 1500000)
    cast(Callable[..., Text], getattr(axis, "set_xlabel"))(
        "Kandidat-Kontext-Auswertungen\n(Oracle: API-Anfragen)"
    )
    for i, name in enumerate(names):
        cast(Callable[..., object], getattr(timing, "scatter"))(
            [r.elapsed * 1000 for r in groups[name]],
            [i - 0.12, i, i + 0.12],
            s=38,
            color=INK,
        )
    style_axis(timing, names)
    cast(Callable[..., object], getattr(timing, "set_yticklabels"))([])
    cast(Callable[..., None], getattr(timing, "set_xscale"))("log")
    cast(Callable[..., Text], getattr(timing, "set_xlabel"))(
        "Gemessene Suchzeit (ms)\nohne Interpreter-/Django-Start"
    )
    cast(Callable[..., Text], getattr(figure, "text"))(
        0.04,
        0.12,
        "Untersuchungen: 460 HMACs/Versuch; mit zusätzlich bekannten Patienten-Token 368. Gleiche Ergebnisse, weniger innere HMACs.",
        fontsize=10,
    )
    cast(Callable[..., Text], getattr(figure, "text"))(
        0.04,
        0.075,
        "Ein serieller Worker · Intel i5-12450H · Python 3.12.14 · Fremdlast unkontrolliert · kurze Laufzeiten besonders rauschanfällig",
        fontsize=10,
    )
    figures["04_computational_cost"] = figure
    return figures


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runtime", required=True)
    args = parser.parse_args()
    initialize(args.runtime)
    from endoreg_db.utils.file_operations import atomic_write_file
    from endoreg_db.utils.paths import get_runtime_paths

    source = Path(__file__).with_name("evidence") / "scores.json"
    rows = TypeAdapter(list[Score]).validate_json(source.read_text())
    output = get_runtime_paths().export_dir / "reidentification" / "figures"
    figures = build(rows)
    pdf = BytesIO()
    with PdfPages(pdf) as pages:
        for name, figure in figures.items():
            cast(Callable[..., None], getattr(pages, "savefig"))(figure)
            for extension in ("png", "svg"):
                buffer = BytesIO()
                cast(Callable[..., None], getattr(figure, "savefig"))(
                    buffer, format=extension, dpi=180
                )
                atomic_write_file(
                    destination=output / f"{name}.{extension}",
                    content=[buffer.getvalue()],
                )
            plt.close(figure)
    atomic_write_file(
        destination=output / "reidentification_figures.pdf", content=[pdf.getvalue()]
    )
    print(output)


if __name__ == "__main__":
    main()
