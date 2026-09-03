from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import date
from pathlib import Path
import re
import subprocess
import sys
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BASELINE = PROJECT_ROOT / "quality" / "dead_code_baseline.yml"
VULTURE_LINE = re.compile(
    r"^(?P<path>.+?):(?P<line>\d+): (?P<message>.+) "
    r"\((?P<confidence>\d+)% confidence, (?P<size>\d+) lines?\)$"
)


class DeadCodeToolConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    paths: list[str] = Field(min_length=1)
    exclude: list[str] = Field(default_factory=list)
    min_confidence: int = Field(ge=0, le=100)


class AcceptedDeadCodeFinding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1)
    message: str = Field(min_length=1)
    confidence: int = Field(ge=0, le=100)
    occurrences: int = Field(default=1, ge=1)
    classification: Literal[
        "compatibility_contract",
        "framework_contract",
        "protocol_signature",
        "typing_only",
    ]
    reason: str = Field(min_length=1)
    owner: str = Field(min_length=1)
    review_after: date


class DeadCodeDeletionCandidate(BaseModel):
    """One investigated removal candidate that is not a Vulture exception."""

    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1)
    line_range: str = Field(min_length=1)
    symbol: str = Field(min_length=1)
    classification: Literal[
        "confirmed_dead",
        "compatibility_contract",
        "uncertain",
    ]
    evidence: list[str] = Field(min_length=1)
    reason: str = Field(min_length=1)
    recommended_action: Literal["remove", "verify_consumers"]
    risk: Literal["low", "medium", "high"]
    owner: str = Field(min_length=1)
    review_after: date


class DeadCodeBaseline(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"] = "1.0"
    tool: DeadCodeToolConfig
    accepted_findings: tuple[AcceptedDeadCodeFinding, ...] = ()
    deletion_candidates: tuple[DeadCodeDeletionCandidate, ...] = ()

    @property
    def confirmed_deletion_count(self) -> int:
        return sum(
            item.classification == "confirmed_dead" for item in self.deletion_candidates
        )


@dataclass(frozen=True, slots=True)
class VultureFinding:
    path: str
    line: int
    message: str
    confidence: int
    size: int

    @property
    def key(self) -> tuple[str, str, int]:
        return (self.path, self.message, self.confidence)


@dataclass(frozen=True, slots=True)
class DeadCodeComparison:
    unexpected: tuple[VultureFinding, ...]
    stale: tuple[AcceptedDeadCodeFinding, ...]
    expired: tuple[AcceptedDeadCodeFinding, ...]

    @property
    def is_clean(self) -> bool:
        return not (self.unexpected or self.stale or self.expired)


def load_baseline(path: Path) -> DeadCodeBaseline:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    return DeadCodeBaseline.model_validate(raw)


def parse_vulture_output(output: str) -> tuple[VultureFinding, ...]:
    findings: list[VultureFinding] = []
    for raw_line in output.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        match = VULTURE_LINE.fullmatch(line)
        if match is None:
            raise ValueError(f"Unrecognized Vulture output: {line}")
        findings.append(
            VultureFinding(
                path=match.group("path"),
                line=int(match.group("line")),
                message=match.group("message"),
                confidence=int(match.group("confidence")),
                size=int(match.group("size")),
            )
        )
    return tuple(findings)


def compare_findings(
    findings: tuple[VultureFinding, ...],
    baseline: DeadCodeBaseline,
    *,
    today: date,
) -> DeadCodeComparison:
    findings_by_key: dict[tuple[str, str, int], list[VultureFinding]] = {}
    for finding in findings:
        findings_by_key.setdefault(finding.key, []).append(finding)

    accepted_by_key = {
        (item.path, item.message, item.confidence): item
        for item in baseline.accepted_findings
    }
    if len(accepted_by_key) != len(baseline.accepted_findings):
        raise ValueError("Dead-code baseline contains duplicate finding keys.")

    return DeadCodeComparison(
        unexpected=tuple(
            finding
            for key, key_findings in findings_by_key.items()
            for finding in key_findings[
                accepted_by_key[key].occurrences if key in accepted_by_key else 0 :
            ]
        ),
        stale=tuple(
            item
            for key, item in accepted_by_key.items()
            if len(findings_by_key.get(key, ())) < item.occurrences
        ),
        expired=tuple(
            item for item in baseline.accepted_findings if item.review_after < today
        ),
    )


def run_vulture(config: DeadCodeToolConfig) -> tuple[VultureFinding, ...]:
    command = [
        sys.executable,
        "-m",
        "vulture",
        *config.paths,
        "--min-confidence",
        str(config.min_confidence),
        "--sort-by-size",
    ]
    if config.exclude:
        command.extend(("--exclude", ",".join(config.exclude)))

    result = subprocess.run(
        command,
        cwd=PROJECT_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode not in {0, 3}:
        raise RuntimeError(
            f"Vulture failed with exit code {result.returncode}: {result.stderr.strip()}"
        )
    if result.stderr.strip():
        raise RuntimeError(f"Vulture wrote to stderr: {result.stderr.strip()}")
    return parse_vulture_output(result.stdout)


def _print_comparison(comparison: DeadCodeComparison) -> None:
    for finding in comparison.unexpected:
        print(
            f"NEW {finding.path}:{finding.line}: {finding.message} "
            f"({finding.confidence}% confidence)"
        )
    for item in comparison.stale:
        print(f"STALE {item.path}: {item.message} ({item.confidence}% confidence)")
    for item in comparison.expired:
        print(
            f"EXPIRED {item.path}: {item.message} "
            f"(review_after={item.review_after.isoformat()}, owner={item.owner})"
        )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Check Vulture findings against the reviewed dead-code baseline."
    )
    parser.add_argument(
        "--baseline",
        type=Path,
        default=DEFAULT_BASELINE,
        help="Path to the reviewed YAML baseline.",
    )
    args = parser.parse_args()

    baseline = load_baseline(args.baseline.resolve())
    findings = run_vulture(baseline.tool)
    comparison = compare_findings(findings, baseline, today=date.today())
    _print_comparison(comparison)
    if not comparison.is_clean:
        return 1
    print(
        "Dead-code baseline clean: "
        f"{len(findings)} reviewed findings, no new/stale/expired entries; "
        f"{baseline.confirmed_deletion_count} confirmed deletion candidate(s), "
        f"{len(baseline.deletion_candidates)} candidate(s) total."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
