from __future__ import annotations

import logging
from typing import Any, Optional

from django.http import HttpRequest

from endoreg_db.models.operation_log import OperationLog

logger = logging.getLogger(__name__)


def record_operation(
    request: HttpRequest,
    *,
    action: str,
    resource_type: str = "",
    resource_id: Optional[int] = None,
    status_before: Optional[str] = None,
    status_after: Optional[str] = None,
    meta: Optional[dict[str, Any]] = None,
) -> None:
    """
    Create an OperationLog entry from a view.
    """
    user = getattr(request, "user", None)

    try:
        log = OperationLog(
            actor_user=user if getattr(user, "is_authenticated", False) else None,
            actor_username=getattr(user, "username", "") if user else "",
            actor_email=getattr(user, "email", "") if user else "",
            actor_keycloak_id="",  # fill later if you add it to your user model
            action=action,
            http_method=getattr(request, "method", ""),
            path=getattr(request, "path", ""),
            resource_type=resource_type,
            resource_id=resource_id,
            status_before=status_before or "",
            status_after=status_after or "",
            meta=meta or None,
        )
        log.save()
    except Exception:
        # Never kill the main request flow because of logging
        logger.exception(
            "Failed to record operation %s for %s(%s)",
            action,
            resource_type,
            resource_id,
        )


# TODO: will make the name more generic later base don the requirement,after merge
def get_resource_type_from_instance(obj):
    name = obj.__class__.__name__
    '''if name == "VideoFile":
        return "video"
    if name == "RawPdfFile":
        return "pdf"'''
    return name.lower()
