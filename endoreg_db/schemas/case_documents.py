from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, StrictInt


class CaseDocumentMediaType(StrEnum):
    PDF = "pdf"
    VIDEO = "video"


class CaseDocumentAttachmentPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    media_type: CaseDocumentMediaType
    media_id: StrictInt = Field(ge=1)
    patient_examination_id: StrictInt = Field(ge=1)


__all__ = [
    "CaseDocumentAttachmentPayload",
    "CaseDocumentMediaType",
]
