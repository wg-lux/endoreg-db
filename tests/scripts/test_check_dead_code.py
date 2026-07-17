from __future__ import annotations

from datetime import date

import pytest

from scripts.check_dead_code import (
    AcceptedDeadCodeFinding,
    DeadCodeBaseline,
    DeadCodeToolConfig,
    compare_findings,
    parse_vulture_output,
)


def _baseline(
    *findings: AcceptedDeadCodeFinding,
) -> DeadCodeBaseline:
    return DeadCodeBaseline(
        tool=DeadCodeToolConfig(
            paths=["endoreg_db"],
            min_confidence=90,
        ),
        accepted_findings=list(findings),
    )


def _accepted(*, review_after: date = date(2026, 10, 17)) -> AcceptedDeadCodeFinding:
    return AcceptedDeadCodeFinding(
        path="endoreg_db/example.py",
        message="unused variable 'framework_argument'",
        confidence=100,
        classification="framework_contract",
        reason="Required by a framework callback signature.",
        owner="endoreg_db maintainers",
        review_after=review_after,
    )


def test_parse_vulture_output_preserves_evidence() -> None:
    findings = parse_vulture_output(
        "endoreg_db/example.py:12: unused variable 'framework_argument' "
        "(100% confidence, 1 line)\n"
    )

    assert len(findings) == 1
    assert findings[0].path == "endoreg_db/example.py"
    assert findings[0].line == 12
    assert findings[0].size == 1


def test_compare_findings_accepts_reviewed_entry_across_line_changes() -> None:
    finding = parse_vulture_output(
        "endoreg_db/example.py:99: unused variable 'framework_argument' "
        "(100% confidence, 1 line)\n"
    )[0]

    comparison = compare_findings(
        (finding,),
        _baseline(_accepted()),
        today=date(2026, 7, 17),
    )

    assert comparison.is_clean


def test_compare_findings_reports_new_stale_and_expired_entries() -> None:
    unexpected = parse_vulture_output(
        "endoreg_db/new.py:4: unused import 'RemovedType' (90% confidence, 1 line)\n"
    )[0]

    comparison = compare_findings(
        (unexpected,),
        _baseline(_accepted(review_after=date(2026, 7, 16))),
        today=date(2026, 7, 17),
    )

    assert comparison.unexpected == (unexpected,)
    assert len(comparison.stale) == 1
    assert len(comparison.expired) == 1
    assert not comparison.is_clean


def test_compare_findings_rejects_duplicate_baseline_keys() -> None:
    with pytest.raises(ValueError, match="duplicate finding keys"):
        compare_findings(
            (),
            _baseline(_accepted(), _accepted()),
            today=date(2026, 7, 17),
        )
