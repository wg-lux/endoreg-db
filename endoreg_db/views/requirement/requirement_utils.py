import logging
from typing import Any, Tuple

logger = logging.getLogger(__name__)

def safe_evaluate_requirement(requirement, *args, mode: str = "strict", **kwargs) -> Tuple[bool, str, str | None]:
    """
    Wrapper for Requirement.evaluate / evaluate_with_details that NEVER raises.

    Returns:
        met: bool
        details: human-readable explanation
        error: error string or None
    """
    try:
        if hasattr(requirement, "evaluate_with_details"):
            met, details = requirement.evaluate_with_details(*args, mode=mode, **kwargs)
        else:
            met = requirement.evaluate(*args, mode=mode, **kwargs)
            details = "Voraussetzung erfüllt" if met else "Voraussetzung nicht erfüllt"
        error = None

    except (TypeError, ValueError) as e:
        met = False
        details = f"Fehler bei der Bewertung der Voraussetzung: {e}"
        error = f"{e.__class__.__name__}: {e}"
        logger.warning(
            "Requirement '%s' validation error: %s",
            getattr(requirement, "name", "unknown"),
            e,
        )
    except Exception as e:
        met = False
        details = f"Unerwarteter Fehler bei der Bewertung: {e}"
        error = f"{e.__class__.__name__}: {e}"
        logger.exception(
            "Requirement '%s' unexpected error",
            getattr(requirement, "name", "unknown"),
        )

    # normalize details to string
    if not isinstance(details, str):
        try:
            import json
            details = json.dumps(details, ensure_ascii=False, default=str)
        except Exception:
            details = str(details)

    return bool(met), details, error
