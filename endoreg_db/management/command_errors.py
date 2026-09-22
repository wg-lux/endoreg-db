from __future__ import annotations

import logging

from django.core.management.base import CommandError

from endoreg_db.exceptions import (
    InteroperabilityError,
    describe_interoperability_error,
)
from endoreg_db.utils.structured_logging import emit_structured_event


logger = logging.getLogger("endoreg_db.management.commands")


def interoperability_command_error(
    error: InteroperabilityError,
    *,
    command_name: str,
) -> CommandError:
    """Map a known interoperability failure to a safe CLI contract."""

    descriptor = describe_interoperability_error(error)
    emit_structured_event(
        logger,
        "command.rejected",
        level=logging.ERROR,
        command_name=command_name,
        error_code=descriptor.code.value,
        reason=descriptor.log_reason,
        retryable=descriptor.retryable,
    )
    return CommandError(
        f"{descriptor.code.value}: {descriptor.safe_message}",
        returncode=1,
    )


__all__ = ["interoperability_command_error"]
