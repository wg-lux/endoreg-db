"""Local guarded matching receipts; opaque references never contain identity inputs."""

from __future__ import annotations

from enum import StrEnum
from typing import Self
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator


class MatchingValue(BaseModel):
    model_config = ConfigDict(
        strict=True, frozen=True, extra="forbid", hide_input_in_errors=True
    )


class MatchingMetadata(MatchingValue):
    session_id: UUID
    source_centre: str = Field(min_length=1, max_length=255)
    receiver_centre: str = Field(min_length=1, max_length=255)
    audience: str = Field(min_length=1, max_length=255)
    purpose: str = Field(min_length=1, max_length=255)
    scheme: str = Field(min_length=1, max_length=100)
    key_epoch: str = Field(min_length=1, max_length=255)
    circuit: str = Field(min_length=1, max_length=255)
    batch_ref: UUID
    expires_at: AwareDatetime


class MatchingRecordRef(MatchingValue):
    uuid: UUID
    row_version: UUID
    payload_ref: UUID
    centre: str = Field(min_length=1, max_length=255)
    scheme: str = Field(min_length=1, max_length=100)
    key_epoch: str = Field(min_length=1, max_length=255)


class MatchingRequest(MatchingValue):
    metadata: MatchingMetadata
    source: MatchingRecordRef
    candidates: tuple[MatchingRecordRef, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def bindings(self) -> Self:
        meta = self.metadata
        if meta.source_centre == meta.receiver_centre:
            raise ValueError("Matching requires distinct centres")
        if (self.source.centre, self.source.scheme, self.source.key_epoch) != (
            meta.source_centre,
            meta.scheme,
            meta.key_epoch,
        ):
            raise ValueError("Source context mismatch")
        if any(
            candidate.centre != meta.receiver_centre or candidate.scheme != meta.scheme
            for candidate in self.candidates
        ):
            raise ValueError("Candidate context mismatch")
        if len({candidate.uuid for candidate in self.candidates}) != len(
            self.candidates
        ):
            raise ValueError("Duplicate candidate UUID")
        return self


class MatchingOutcome(StrEnum):
    NO_MATCH = "no_match"
    SINGLE_MATCH = "single_match"
    AMBIGUOUS_MATCHES = "ambiguous_matches"
    ABORTED_OR_INCOMPLETE = "aborted_or_incomplete"
    KEY_EPOCH_UNAVAILABLE = "key_epoch_unavailable"


class MatchingResult(MatchingValue):
    metadata: MatchingMetadata
    source: MatchingRecordRef
    outcome: MatchingOutcome
    matches: tuple[MatchingRecordRef, ...] = ()

    @model_validator(mode="after")
    def cardinality(self) -> Self:
        count = len(self.matches)
        if self.outcome == MatchingOutcome.SINGLE_MATCH:
            valid = count == 1
        else:
            valid = count == 0
        if not valid:
            raise ValueError("Outcome and match cardinality disagree")
        return self


class MatchingSignature(MatchingValue):
    centre: str
    key_id: str
    signature: str = Field(pattern=r"^[0-9a-f]{128}$")


def matching_result_bytes(result: MatchingResult) -> bytes:
    """Versioned deterministic signing bytes; both signers sign this exact receipt."""
    return b"endoreg-patient-matching-result-v1\n" + result.model_dump_json().encode(
        "utf-8"
    )
