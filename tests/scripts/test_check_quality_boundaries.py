from __future__ import annotations

from datetime import date

from scripts.check_quality_boundaries import (
    PolicySnapshot,
    QualityBoundaryBaseline,
    QualityBoundaryScanConfig,
    ReviewedPolicySnapshot,
    compare_with_baseline,
    make_snapshot,
    scan_source,
)


def _reviewed(
    snapshot: PolicySnapshot, *, review_after: date
) -> ReviewedPolicySnapshot:
    return ReviewedPolicySnapshot(
        count=snapshot.count,
        fingerprint=snapshot.fingerprint,
        owner="endoreg_db maintainers",
        reason="Existing debt is frozen until its owning cohort removes it.",
        review_after=review_after,
    )


def _baseline(source: str, *, review_after: date) -> QualityBoundaryBaseline:
    findings = scan_source(path="endoreg_db/example.py", source=source)
    return QualityBoundaryBaseline(
        scan=QualityBoundaryScanConfig(paths=["endoreg_db"]),
        broad_exception=_reviewed(
            make_snapshot(findings, kind="broad_exception"),
            review_after=review_after,
        ),
        type_suppression=_reviewed(
            make_snapshot(findings, kind="type_suppression"),
            review_after=review_after,
        ),
    )


def test_scan_source_finds_only_broad_handlers_and_real_ignore_comments() -> None:
    findings = scan_source(
        path="endoreg_db/example.py",
        source='''
def integration_boundary() -> None:
    """The text '# type: ignore' is not a suppression."""
    try:
        operation()
    except OSError:
        raise
    try:
        operation()
    except (LookupError, Exception):
        raise

value = dynamic_value  # pyright: ignore[reportUnknownVariableType, reportAssignmentType]
''',
    )

    assert [(item.kind, item.scope, item.detail) for item in findings] == [
        ("broad_exception", "integration_boundary", "Exception"),
        (
            "type_suppression",
            "<file>",
            "pyright:ignore[reportAssignmentType,reportUnknownVariableType]",
        ),
    ]


def test_bare_except_is_broad_and_causes_baseline_drift() -> None:
    baseline = _baseline("value = 1\n", review_after=date(2026, 7, 18))
    findings = scan_source(
        path="endoreg_db/example.py",
        source="""
def inner_service() -> None:
    try:
        operation()
    except:
        raise
""",
    )

    assert [(item.kind, item.scope, item.detail) for item in findings] == [
        ("broad_exception", "inner_service", "bare except"),
    ]

    broad_exception, type_suppression = compare_with_baseline(
        findings,
        baseline,
        today=date(2026, 7, 17),
    )
    assert broad_exception.actual.count == 1
    assert not broad_exception.is_clean
    assert type_suppression.is_clean


def test_snapshot_is_stable_when_only_source_lines_move() -> None:
    compact = scan_source(
        path="endoreg_db/example.py",
        source="def boundary():\n    try:\n        call()\n    except Exception:\n        raise\n",
    )
    spaced = scan_source(
        path="endoreg_db/example.py",
        source="\n\ndef boundary():\n\n    try:\n        call()\n    except Exception:\n        raise\n",
    )

    assert make_snapshot(compact, kind="broad_exception") == make_snapshot(
        spaced,
        kind="broad_exception",
    )


def test_compare_reports_policy_drift_and_expired_review() -> None:
    original = "value = dynamic  # type: ignore[assignment]\n"
    baseline = _baseline(original, review_after=date(2026, 7, 16))
    changed = scan_source(
        path="endoreg_db/example.py",
        source=(original + "try:\n    operation()\nexcept BaseException:\n    raise\n"),
    )

    comparisons = compare_with_baseline(
        changed,
        baseline,
        today=date(2026, 7, 17),
    )

    assert comparisons[0].kind == "broad_exception"
    assert comparisons[0].actual.count == 1
    assert not comparisons[0].is_clean
    assert comparisons[1].expired
    assert not comparisons[1].is_clean
