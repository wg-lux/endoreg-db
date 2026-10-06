from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from pytest import MonkeyPatch

from endoreg_db.utils import dates

pytestmark = pytest.mark.no_db


@pytest.mark.parametrize(
    "year, month, last_day",
    [(2024, 2, 29), (2023, 2, 28), (2024, 4, 30), (2024, 12, 31)],
)
@pytest.mark.parametrize("select_last_day", [False, True])
def test_random_day_by_month_year_respects_month_length(
    monkeypatch: MonkeyPatch,
    year: int,
    month: int,
    last_day: int,
    select_last_day: bool,
) -> None:
    def select_day(start: int, end: int) -> int:
        assert (start, end) == (1, last_day)
        return end if select_last_day else start

    monkeypatch.setattr(dates, "randint", select_day)

    result = dates.random_day_by_month_year(month, year)

    assert result == date(year, month, last_day if select_last_day else 1)


@pytest.mark.parametrize("month", [0, 13])
def test_random_day_by_month_year_rejects_invalid_month(month: int) -> None:
    with pytest.raises(ValueError):
        dates.random_day_by_month_year(month, 2024)


def test_random_day_by_age_at_date_rejects_invalid_leap_day_replacement() -> None:
    with pytest.raises(ValueError):
        dates.random_day_by_age_at_date(1, date(2024, 2, 29))


def test_random_date_helpers_honor_selected_calendar_boundaries(
    monkeypatch: MonkeyPatch,
) -> None:
    selections = iter((364, 2, 29, 30))

    def select_date_part(_start: int, _end: int) -> int:
        return next(selections)

    monkeypatch.setattr(dates, "randint", select_date_part)

    assert dates.random_day_by_age_at_date(40, date(2024, 3, 1)) == date(
        1983,
        3,
        3,
    )
    assert dates.random_day_by_year(2024) == date(2024, 2, 29)
    assert dates.random_day_by_month_year(4, 2024) == date(2024, 4, 30)


def test_ensure_aware_datetime_handles_naive_and_aware_values() -> None:
    naive = datetime(2026, 1, 2, 3, 4)
    aware = datetime(2026, 1, 2, 3, 4, tzinfo=UTC)

    converted = dates.ensure_aware_datetime(naive)

    assert converted.replace(tzinfo=None) == naive
    assert converted.tzinfo is not None
    assert dates.ensure_aware_datetime(aware) is aware
