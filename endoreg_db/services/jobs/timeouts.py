"""Classify cancellation without parsing third-party error messages."""

from billiard.exceptions import SoftTimeLimitExceeded, TimeLimitExceeded


def is_processing_timeout(error: object) -> bool:
    seen: set[int] = set()
    pending = [error] if isinstance(error, BaseException) else []
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        if isinstance(
            current, (TimeoutError, SoftTimeLimitExceeded, TimeLimitExceeded)
        ):
            return True
        cause = current.__cause__
        context = current.__context__
        if cause is not None:
            pending.append(cause)
        elif context is not None and not current.__suppress_context__:
            pending.append(context)
    return False
