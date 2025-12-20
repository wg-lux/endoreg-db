import logging
from typing import Any, Tuple, TYPE_CHECKING
import json

from endoreg_db.models.requirement.requirement_error import (
    RequirementEvaluationError,
)

if TYPE_CHECKING:
    from endoreg_db.models.requirement.requirement import Requirement

logger = logging.getLogger(__name__)


def safe_evaluate_requirement(
    requirement: "Requirement",
    *args: Any,
    mode: str = "strict",
    **kwargs: Any,
) -> Tuple[bool, str, str | None]:
    """
    Wrapper für Requirement.evaluate / evaluate_with_details, der NIE Exceptions durchlässt.

    Returns:
        met: bool
        details: human-readable explanation (für UI)
        error: technischer Fehlerstring oder None
    """

    try:
        if hasattr(requirement, "evaluate_with_details"):
            met, details = requirement.evaluate_with_details(*args, mode=mode, **kwargs)
        else:
            met = requirement.evaluate(*args, **kwargs)
            details = "Voraussetzung erfüllt" if met else "Voraussetzung nicht erfüllt"
        error = None

    except RequirementEvaluationError as e:
        ctx = e.context
        req_obj = ctx.requirement or requirement

        # sprechbarer Name aus DB (Beschreibung bevorzugen, sonst name)
        display_name = (
            getattr(req_obj, "description", None)
            or getattr(req_obj, "name", None)
            or "unbekannte Voraussetzung"
        )

        # falls Exception schon eine user_message mitbringt: die nehmen
        if ctx.user_message:
            details = ctx.user_message
        else:
            details = (
                f"Die Voraussetzung „{display_name}“ konnte nicht ausgewertet werden."
            )

        # technischer Fehlerstring für Logs/Debug
        error = f"{ctx.code}: {ctx.technical_message}"
        logger.warning(
            "Requirement '%s' (code=%s) validation error: %s",
            getattr(req_obj, "name", "unknown"),
            ctx.code,
            ctx.technical_message,
        )

        met = False

    except Exception as e:
        met = False
        display_name = getattr(requirement, "description", None) or getattr(
            requirement, "name", "unbekannte Voraussetzung"
        )
        details = f"Bei der Auswertung der Voraussetzung „{display_name}“ ist ein interner Fehler aufgetreten."
        error = f"{e.__class__.__name__}: {e}"
        logger.exception(
            "Requirement '%s' unexpected error",
            getattr(requirement, "name", "unknown"),
        )

    # normalize details to string
    if not isinstance(details, str):
        try:
            details = json.dumps(details, ensure_ascii=False, default=str)
        except Exception:
            details = str(details)

    return bool(met), details, error
