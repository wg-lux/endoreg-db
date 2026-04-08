from __future__ import annotations

import json
import logging
from typing import Any


logger = logging.getLogger("endoreg_db.hub.audit")


def _request_user_repr(user: Any) -> str | None:
    if user is None:
        return None
    if not getattr(user, "is_authenticated", False):
        return None
    username = str(getattr(user, "username", "") or "").strip()
    if username:
        return username
    user_id = getattr(user, "pk", None)
    if user_id is None:
        return None
    return str(user_id)


def emit_hub_audit_event(event: str, **payload: Any) -> None:
    body = {"event": event, **payload}
    if "request_user" in body:
        body["request_user"] = _request_user_repr(body["request_user"])
    try:
        logger.info(json.dumps(body, default=str, sort_keys=True))
    except Exception:
        logger.exception("Failed to emit hub audit event %s", event)
