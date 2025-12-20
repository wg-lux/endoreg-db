from dataclasses import dataclass
from typing import Optional, Any, Mapping, TYPE_CHECKING

if TYPE_CHECKING:
    from endoreg_db.models.requirement.requirement import Requirement


@dataclass
class RequirementEvaluationErrorContext:
    """
    Extra context for evaluation errors.

    - requirement: DB object (so we can use names, groups, translations, etc.)
    - code: stable internal code, preferred over free-text matching
    - user_message: optional pre-formatted text for UI
    - meta: optional arbitrary payload for logging / debugging
    """

    requirement: "Requirement"
    code: str
    technical_message: str
    user_message: Optional[str] = None
    description: Optional[str] = None
    meta: Optional[Mapping[str, Any]] = None


class RequirementEvaluationError(Exception):
    """
    Domain-level error that signals: this requirement could be evaluated.

    It carries:
    - a Requirement instance (from DB)
    - a stable error code
    - a technical message for logs
    - an optional user-ready message
    """

    def __init__(
        self,
        requirement: "Requirement",
        code: str,
        technical_message: str,
        user_message: Optional[str] = None,
        meta: Optional[Mapping[str, Any]] = None,
        *args,
    ) -> None:
        self.context = RequirementEvaluationErrorContext(
            requirement=requirement,
            code=code,
            technical_message=technical_message,
            user_message=user_message,
            meta=meta,
        )
        # Base Exception message = technical message
        super().__init__(technical_message, *args)

    @property
    def requirement(self) -> "Requirement":
        return self.context.requirement

    @property
    def requirement_name(self) -> str:
        # This is the DB name, *not* the internal Python repr
        return getattr(self.context.requirement, "name", "unknown")

    @property
    def requirement_description(self) -> str:
        return getattr(self.context.requirement, "description")

    @property
    def code(self) -> str:
        return self.context.code

    @property
    def technical_message(self) -> str:
        return self.context.technical_message

    @property
    def user_message(self) -> Optional[str]:
        return self.context.user_message

    @property
    def meta(self) -> Optional[Mapping[str, Any]]:
        return self.context.meta
