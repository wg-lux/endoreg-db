from __future__ import annotations

# pyright: reportPrivateUsage=false
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from endoreg_db.services.hub import audit


def test_request_user_repr_handles_anonymous_and_authenticated_users() -> None:
    assert audit._request_user_repr(None) is None
    assert (
        audit._request_user_repr(
            SimpleNamespace(is_authenticated=False, username="node")
        )
        is None
    )

    user_with_username = SimpleNamespace(
        is_authenticated=True,
        username=" node-user ",
        pk=7,
    )
    assert audit._request_user_repr(user_with_username) == "node-user"

    user_with_missing_username = SimpleNamespace(
        is_authenticated=True,
        username="",
        pk=99,
    )
    assert audit._request_user_repr(user_with_missing_username) == "99"


def test_emit_hub_audit_event_uses_structured_event_with_normalized_user() -> None:
    user = SimpleNamespace(is_authenticated=True, username=" uploader ")

    with patch(
        "endoreg_db.services.hub.audit.emit_structured_event"
    ) as emit_structured_event:
        audit.emit_hub_audit_event(
            "hub.event",
            request_user=user,
            transfer_key="k1",
        )

    assert emit_structured_event.call_count == 1
    call_args = emit_structured_event.call_args
    assert call_args.args[0] is audit.logger
    assert call_args.args[1] == "hub.event"
    assert call_args.kwargs["request_user"] == "uploader"
    assert call_args.kwargs["transfer_key"] == "k1"


def test_emit_hub_audit_event_falls_back_on_structured_logger_exception() -> None:
    logger = MagicMock()
    logger.exception = MagicMock()
    with (
        patch(
            "endoreg_db.services.hub.audit.emit_structured_event",
            side_effect=RuntimeError,
        ),
        patch("endoreg_db.services.hub.audit.logger", logger),
    ):
        audit.emit_hub_audit_event("hub.event", request_user=None)

    logger.exception.assert_called_once()
