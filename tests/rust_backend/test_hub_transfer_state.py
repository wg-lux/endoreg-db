from __future__ import annotations

from typing import cast

import pytest

from endoreg_db.models.hub.outbound_hub_transfer_job import OutboundHubTransferJob
from endoreg_db.models.hub.transfer_job import TransferJob
from endoreg_db.models.state.hub_transfer import HubExportFailureClass
from endoreg_db.utils import rust_backend

pytestmark = pytest.mark.no_db


def test_persistence_uses_the_only_failure_enum() -> None:
    assert OutboundHubTransferJob.FailureClass is HubExportFailureClass
    assert (
        set(HubExportFailureClass.values)
        == rust_backend.native_state_vocabularies()["hub_failure_class"]
    )


Status = OutboundHubTransferJob.LocalStatus
Event = rust_backend.HubTransferEvent


def _reduce(
    current: str,
    event: rust_backend.HubTransferEvent,
    *,
    current_failure: str | None = None,
    remote: str | None = None,
    failure: str | None = None,
) -> tuple[str, str | None, bool]:
    return rust_backend.reduce_hub_transfer_event(
        current_state=current,
        current_failure_class=current_failure,
        event=event,
        remote_status=remote,
        failure_class=failure,
    )


def test_event_mirror_matches_native_vocabulary() -> None:
    assert set(Event) == rust_backend.native_state_vocabularies()["hub_transfer_event"]


@pytest.mark.parametrize("remote", TransferJob.TransferStatus.values)
def test_completed_is_absorbing_for_every_receiver_status(remote: str) -> None:
    assert _reduce("completed", Event.ACKNOWLEDGE, remote=remote) == (
        "completed",
        None,
        False,
    )


@pytest.mark.parametrize("failure", HubExportFailureClass.values)
def test_failures_are_native_and_cannot_downgrade_completion(failure: str) -> None:
    assert _reduce("uploading", Event.FAIL, failure=failure) == (
        "failed",
        failure,
        True,
    )
    assert _reduce("completed", Event.FAIL, failure=failure) == (
        "completed",
        None,
        False,
    )
    assert rust_backend.hub_failure_is_retryable(failure) == (
        failure == "transient_retry"
    )


@pytest.mark.parametrize(
    "remote,expected",
    [
        ("applied", ("completed", None, True)),
        ("awaiting_media", ("awaiting_media", None, True)),
        ("running", ("registering", None, True)),
        ("failed", ("failed", "configuration_rejection", True)),
        ("inconsistent", ("failed", "integrity_inconsistency", True)),
        ("lost", ("failed", "integrity_inconsistency", True)),
    ],
)
def test_in_flight_acknowledgements_reduce_through_rust(
    remote: str, expected: tuple[str, str | None, bool]
) -> None:
    assert _reduce("registering", Event.ACKNOWLEDGE, remote=remote) == expected


@pytest.mark.parametrize("current", [Status.QUEUED, Status.FAILED])
@pytest.mark.parametrize("remote", TransferJob.TransferStatus.values)
def test_superseded_attempts_accept_only_hub_completion(
    current: str, remote: str
) -> None:
    applied = remote == "applied"
    expected = ("completed", None, True) if applied else (current, None, False)
    assert _reduce(current, Event.ACKNOWLEDGE, remote=remote) == expected


@pytest.mark.parametrize(
    "previous",
    ["configuration_rejection", "authorization_denial", "integrity_inconsistency"],
)
@pytest.mark.parametrize("failure", HubExportFailureClass.values)
def test_first_non_retryable_failure_is_kept_and_duplicates_are_no_ops(
    previous: str, failure: str
) -> None:
    assert _reduce("failed", Event.FAIL, current_failure=previous, failure=failure) == (
        "failed",
        previous,
        False,
    )
    assert _reduce("failed", Event.RETRY, current_failure=previous) == (
        "failed",
        previous,
        False,
    )
    assert _reduce("failed", Event.OPERATOR_RETRY, current_failure=previous) == (
        "queued",
        None,
        True,
    )


@pytest.mark.parametrize(
    "current,event,expected",
    [
        (Status.MARKED, Event.QUEUE, ("queued", None, True)),
        (Status.QUEUED, Event.QUEUE, ("queued", None, False)),
        (Status.QUEUED, Event.REDISPATCH, ("queued", None, True)),
        (Status.QUEUED, Event.START_REGISTRATION, ("registering", None, True)),
        (Status.MARKED, Event.START_REGISTRATION, ("marked", None, False)),
        (Status.AWAITING_MEDIA, Event.START_UPLOAD, ("uploading", None, True)),
        (Status.REGISTERING, Event.START_UPLOAD, ("registering", None, False)),
        (
            Status.UPLOADING,
            Event.INELIGIBLE,
            ("failed", "configuration_rejection", True),
        ),
        (Status.MARKED, Event.INELIGIBLE, ("marked", None, False)),
    ],
)
def test_lifecycle_events_apply_only_from_their_source_states(
    current: str,
    event: rust_backend.HubTransferEvent,
    expected: tuple[str, str | None, bool],
) -> None:
    assert _reduce(current, event) == expected


@pytest.mark.parametrize(
    "current,event,current_failure,remote,failure",
    [
        ("unknown", Event.QUEUE, None, None, None),
        ("queued", Event.QUEUE, "unknown", None, None),
        ("completed", Event.ACKNOWLEDGE, None, "unknown", None),
        ("completed", Event.FAIL, None, None, "unknown"),
        ("queued", Event.ACKNOWLEDGE, None, "applied", "transient_retry"),
        ("queued", Event.ACKNOWLEDGE, None, None, None),
        ("queued", Event.FAIL, None, None, None),
        ("queued", Event.QUEUE, None, "applied", None),
        ("marked", Event.ACKNOWLEDGE, None, "applied", None),
    ],
)
def test_unknown_ambiguous_or_undispatched_input_fails_closed(
    current: str,
    event: rust_backend.HubTransferEvent,
    current_failure: str | None,
    remote: str | None,
    failure: str | None,
) -> None:
    with pytest.raises(ValueError):
        _reduce(
            current,
            event,
            current_failure=current_failure,
            remote=remote,
            failure=failure,
        )


def test_unknown_event_token_fails_closed() -> None:
    with pytest.raises(ValueError):
        _reduce("queued", cast(rust_backend.HubTransferEvent, "resurrect"))


def test_missing_native_capability_has_no_python_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unavailable(name: str, contract_version: str) -> bool:
        return False

    monkeypatch.setattr(rust_backend, "has_native_capability", unavailable)
    with pytest.raises(RuntimeError, match="hub_transfer_state_v2"):
        _reduce("uploading", Event.ACKNOWLEDGE, remote="applied")
    with pytest.raises(RuntimeError, match="hub_transfer_state_v2"):
        rust_backend.hub_failure_is_retryable("transient_retry")
