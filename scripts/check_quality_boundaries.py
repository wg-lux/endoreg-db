from __future__ import annotations

import argparse
import ast
from dataclasses import dataclass
from datetime import date
from hashlib import sha256
from io import StringIO
from pathlib import Path
import re
import tokenize
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BASELINE = PROJECT_ROOT / "quality" / "quality_boundary_baseline.yml"
SUPPRESSION_PATTERN = re.compile(
    r"(?P<tool>pyright|type)\s*:\s*ignore"
    r"(?:\s*\[(?P<codes>[^\]]+)\])?",
    re.IGNORECASE,
)

PolicyKind = Literal["broad_exception", "type_suppression"]


class QualityBoundaryScanConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    paths: list[str] = Field(min_length=1)
    excluded_path_parts: list[str] = Field(default_factory=list)


class ReviewedPolicySnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    count: int = Field(ge=0)
    fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    owner: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    review_after: date


class QualityBoundaryBaseline(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"] = "1.0"
    scan: QualityBoundaryScanConfig
    broad_exception: ReviewedPolicySnapshot
    type_suppression: ReviewedPolicySnapshot


@dataclass(frozen=True, slots=True)
class PolicyFinding:
    kind: PolicyKind
    path: str
    scope: str
    detail: str

    @property
    def key(self) -> str:
        return "\0".join((self.path, self.scope, self.detail))


@dataclass(frozen=True, slots=True)
class PolicySnapshot:
    count: int
    fingerprint: str


@dataclass(frozen=True, slots=True)
class PolicyDrift:
    kind: PolicyKind
    expected: PolicySnapshot
    actual: PolicySnapshot
    expired: bool

    @property
    def is_clean(self) -> bool:
        return self.expected == self.actual and not self.expired


class _BroadExceptionVisitor(ast.NodeVisitor):
    def __init__(self, *, path: str) -> None:
        self._path = path
        self._scope: list[str] = []
        self.findings: list[PolicyFinding] = []

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._visit_scoped(node.name, node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_scoped(node.name, node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_scoped(node.name, node)

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        broad_types = _broad_exception_types(node.type)
        for exception_type in broad_types:
            self.findings.append(
                PolicyFinding(
                    kind="broad_exception",
                    path=self._path,
                    scope=".".join(self._scope) or "<module>",
                    detail=exception_type,
                )
            )
        self.generic_visit(node)

    def _visit_scoped(
        self,
        name: str,
        node: ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef,
    ) -> None:
        self._scope.append(name)
        self.generic_visit(node)
        self._scope.pop()


def _broad_exception_types(node: ast.expr | None) -> tuple[str, ...]:
    if node is None:
        return ("bare except",)
    if isinstance(node, ast.Name) and node.id in {"Exception", "BaseException"}:
        return (node.id,)
    if isinstance(node, ast.Tuple):
        return tuple(
            exception_type
            for element in node.elts
            for exception_type in _broad_exception_types(element)
        )
    return ()


def _normalized_suppression(comment: str) -> tuple[str, ...]:
    suppressions: list[str] = []
    for match in SUPPRESSION_PATTERN.finditer(comment):
        tool = match.group("tool").lower()
        raw_codes = match.group("codes")
        if raw_codes is None:
            suppressions.append(f"{tool}:ignore")
            continue
        codes = sorted(code.strip() for code in raw_codes.split(",") if code.strip())
        suppressions.append(f"{tool}:ignore[{','.join(codes)}]")
    return tuple(suppressions)


def scan_source(*, path: str, source: str) -> tuple[PolicyFinding, ...]:
    tree = ast.parse(source, filename=path)
    visitor = _BroadExceptionVisitor(path=path)
    visitor.visit(tree)

    findings = list(visitor.findings)
    tokens = tokenize.generate_tokens(StringIO(source).readline)
    for token in tokens:
        if token.type != tokenize.COMMENT:
            continue
        for suppression in _normalized_suppression(token.string):
            findings.append(
                PolicyFinding(
                    kind="type_suppression",
                    path=path,
                    scope="<file>",
                    detail=suppression,
                )
            )
    return tuple(findings)


def scan_repository(
    config: QualityBoundaryScanConfig,
    *,
    project_root: Path = PROJECT_ROOT,
) -> tuple[PolicyFinding, ...]:
    findings: list[PolicyFinding] = []
    excluded = set(config.excluded_path_parts)
    for configured_path in config.paths:
        root = project_root / configured_path
        for source_path in sorted(root.rglob("*.py")):
            relative_path = source_path.relative_to(project_root)
            if excluded.intersection(relative_path.parts):
                continue
            findings.extend(
                scan_source(
                    path=relative_path.as_posix(),
                    source=source_path.read_text(encoding="utf-8"),
                )
            )
    return tuple(findings)


def make_snapshot(
    findings: tuple[PolicyFinding, ...],
    *,
    kind: PolicyKind,
) -> PolicySnapshot:
    keys = sorted(finding.key for finding in findings if finding.kind == kind)
    payload = "\n".join(keys).encode("utf-8")
    return PolicySnapshot(count=len(keys), fingerprint=sha256(payload).hexdigest())


def compare_with_baseline(
    findings: tuple[PolicyFinding, ...],
    baseline: QualityBoundaryBaseline,
    *,
    today: date,
) -> tuple[PolicyDrift, PolicyDrift]:
    broad_exception = PolicyDrift(
        kind="broad_exception",
        expected=PolicySnapshot(
            count=baseline.broad_exception.count,
            fingerprint=baseline.broad_exception.fingerprint,
        ),
        actual=make_snapshot(findings, kind="broad_exception"),
        expired=baseline.broad_exception.review_after < today,
    )
    type_suppression = PolicyDrift(
        kind="type_suppression",
        expected=PolicySnapshot(
            count=baseline.type_suppression.count,
            fingerprint=baseline.type_suppression.fingerprint,
        ),
        actual=make_snapshot(findings, kind="type_suppression"),
        expired=baseline.type_suppression.review_after < today,
    )
    return broad_exception, type_suppression


def load_baseline(path: Path) -> QualityBoundaryBaseline:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    return QualityBoundaryBaseline.model_validate(raw)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Reject unreviewed drift in broad exception handlers and type suppressions."
        )
    )
    parser.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    args = parser.parse_args()

    baseline = load_baseline(args.baseline.resolve())
    findings = scan_repository(baseline.scan)
    comparisons = compare_with_baseline(findings, baseline, today=date.today())
    failed = False
    for comparison in comparisons:
        if comparison.is_clean:
            continue
        failed = True
        print(
            f"DRIFT {comparison.kind}: expected "
            f"count={comparison.expected.count} "
            f"fingerprint={comparison.expected.fingerprint}; actual "
            f"count={comparison.actual.count} "
            f"fingerprint={comparison.actual.fingerprint}"
        )
        if comparison.expired:
            reviewed = getattr(baseline, comparison.kind)
            print(
                f"EXPIRED {comparison.kind}: "
                f"review_after={reviewed.review_after.isoformat()}, "
                f"owner={reviewed.owner}"
            )
    if failed:
        return 1
    print(
        "Quality-boundary baseline clean: "
        f"{baseline.broad_exception.count} broad handlers and "
        f"{baseline.type_suppression.count} type suppressions reviewed."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
