from datetime import datetime, timedelta, timezone
from threading import Event

import pytest
from django.db import OperationalError

from endoreg_db.services.imports.lease import ImportLeaseHeartbeat, owns_live_lease


@pytest.mark.parametrize(
    ("owner", "token", "seconds", "expected"),
    [
        ("owner", 2, 1, True),
        ("other", 2, 1, False),
        ("owner", 1, 1, False),
        ("owner", 2, 0, False),
        ("owner", 2, -1, False),
        ("owner", 2, None, False),
    ],
)
def test_live_ownership_requires_owner_token_and_unexpired_database_time(
    owner: str,
    token: int,
    seconds: int | None,
    expected: bool,
) -> None:
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    assert (
        owns_live_lease(
            current_owner=owner,
            expected_owner="owner",
            current_token=token,
            expected_token=2,
            expires_at=None if seconds is None else now + timedelta(seconds=seconds),
            now=now,
        )
        is expected
    )


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("outage", [True, False])
def test_background_failure_reaches_guard_without_another_renewal(outage: bool) -> None:
    failure = OperationalError("unavailable") if outage else ValueError("broken")
    observed = Event()
    calls = 0

    def renew() -> None:
        nonlocal calls
        calls += 1
        raise failure

    with ImportLeaseHeartbeat(
        renew=renew,
        lost_error=RuntimeError,
        name="test-import-heartbeat",
        interval_seconds=0.01,
        on_failure=lambda _: observed.set(),
    ) as heartbeat:
        assert observed.wait(timeout=2)
        with pytest.raises(OperationalError if outage else RuntimeError) as error:
            heartbeat.guard()
        if outage:
            assert error.value is failure
        else:
            assert error.value.__cause__ is failure
    assert calls == 1


@pytest.mark.parametrize("interval", [0.0, -1.0])
def test_nonpositive_heartbeat_interval_is_rejected(interval: float) -> None:
    with pytest.raises(ValueError, match="positive"):
        ImportLeaseHeartbeat(
            renew=lambda: None,
            lost_error=RuntimeError,
            name="invalid-heartbeat",
            interval_seconds=interval,
        )
