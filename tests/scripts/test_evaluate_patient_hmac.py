"""Bounded evaluation contracts; hashes come from the application helper."""

import pytest

from scripts.evaluate_patient_hmac import evaluate

pytestmark = pytest.mark.no_db


def test_known_source_key_recovers_candidate_and_other_contexts_do_not() -> None:
    rows = evaluate(repeats=1)
    assert len(rows) == 5
    for row in rows:
        assert row.attempts == 366
        assert row.elapsed_seconds >= 0
        if row.scenario in ("shared_source_context", "local_source_compromised"):
            assert (row.matches, row.first_match_attempt, row.recovered_date) == (
                1,
                196,
                "1980-07-14",
            )
        else:
            assert (row.matches, row.first_match_attempt, row.recovered_date) == (
                0,
                None,
                None,
            )


@pytest.mark.parametrize("repeats", [0, -1])
def test_invalid_repeat_count(repeats: int) -> None:
    with pytest.raises(ValueError, match="positive"):
        evaluate(repeats)
