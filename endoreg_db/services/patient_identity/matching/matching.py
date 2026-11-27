"""Local-role orchestration for authorized exact-tuple comparison.

Each endpoint registers the same public request with its own local snapshots.
Only B receives the aggregate outcome. A countersigns B's assertion after its
own worker succeeds; that signature is not independent proof of B's output.
No endpoint receives both parties' private inputs through this service.
"""

from dataclasses import dataclass

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from django.db import transaction
from django.views.decorators.debug import sensitive_variables

from endoreg_db.models.hub.patient_matching import PatientMatchingSession
from endoreg_db.utils.hashs import get_bytes_hash
from endoreg_db.schemas.patient_matching import (
    MatchingOutcome,
    MatchingRequest,
    MatchingResult,
    MatchingSignature,
)
from endoreg_db.services.patient_identity.matching import matching_backend
from endoreg_db.services.patient_identity.matching.matching_acceptance import (
    MatchingOperator,
    MatchingPolicy,
    authorize_operator,
    record_completion,
    sign_result,
    validate_matching_request,
    verify_signatures,
)
from endoreg_db.services.patient_identity.matching.matching_inputs import (
    CandidateMatchingInput,
    MatchingAuthorization,
    MatchingInputError,
    SourceMatchingInput,
    assert_candidate_unchanged,
    assert_source_unchanged,
)


@dataclass(frozen=True, repr=False)
class SourceMatchingRecords:
    snapshot: SourceMatchingInput
    authorization: MatchingAuthorization
    allowed_key_epochs: frozenset[str]

    def validate(self, request: MatchingRequest) -> None:
        if request.source != self.snapshot.reference:
            raise MatchingInputError("Source snapshot differs from authorized request")
        assert_source_unchanged(
            self.snapshot,
            authorization=self.authorization,
            allowed_key_epochs=self.allowed_key_epochs,
        )


@dataclass(frozen=True, repr=False)
class CandidateMatchingRecords:
    snapshots: tuple[CandidateMatchingInput, ...]
    authorization: MatchingAuthorization

    def validate(self, request: MatchingRequest) -> None:
        if tuple(s.reference for s in self.snapshots) != request.candidates:
            raise MatchingInputError(
                "Candidate snapshots differ from authorized request"
            )
        for snapshot in self.snapshots:
            if snapshot.source_center_key != request.metadata.source_centre:
                raise MatchingInputError("Candidate uses a different source centre")
            assert_candidate_unchanged(snapshot, authorization=self.authorization)


@transaction.atomic
def _claim(request: MatchingRequest, actor: str) -> None:
    session = PatientMatchingSession.objects.select_for_update().get(
        session_id=request.metadata.session_id
    )
    if MatchingRequest.model_validate_json(session.request_json) != request:
        raise ValueError("Worker request differs from registered session")
    if (
        PatientMatchingSession.objects.filter(
            pk=session.pk, execution_started=False, consumed=False, completion_json=""
        ).update(execution_started=True, completed_by=actor)
        != 1
    ):
        raise ValueError("Matching execution already claimed or consumed")


@sensitive_variables()
def run_matching_worker(
    request: MatchingRequest,
    *,
    job: matching_backend.BackendJob,
    worker: MatchingOperator,
    policy: MatchingPolicy,
    records: SourceMatchingRecords | CandidateMatchingRecords,
) -> MatchingResult | None:
    """Run one enrolled role once; errors never produce a completed nonmatch.

    Input/policy errors raise before launching. A backend failure on B is recorded
    as incomplete, provided local bindings still validate. Lost/stale bindings raise
    and leave the claimed session unusable. Restart requires a new authorization.
    """
    actor = authorize_operator(worker, "execute")
    validate_matching_request(request, policy, require_epoch=True)
    records.validate(request)
    pins = dict(job.artifacts)
    if (
        job.public_epoch != request.metadata.key_epoch
        or len(job.payload_sizes) != len(request.candidates)
        or pins.get("Programs/Source/patient_matching_resolver.mpc") != policy.circuit
    ):
        raise ValueError(
            "Worker artifacts or dimensions differ from authorized request"
        )
    context = bytes.fromhex(
        get_bytes_hash(
            b"endoreg-patient-matching-request-v1\n"
            + request.model_dump_json().encode("utf-8")
        )
    )
    if isinstance(records, SourceMatchingRecords):
        if job.party != 0 or policy.local_centre != request.metadata.source_centre:
            raise ValueError("Source role mismatch")
        if job.key_size != len(records.snapshot.key):
            raise ValueError("Source key length mismatch")
        private_input = records.snapshot.key + records.snapshot.target + context
    else:
        if job.party != 1 or policy.local_centre != request.metadata.receiver_centre:
            raise ValueError("Candidate role mismatch")
        if job.payload_sizes != tuple(len(s.payload) for s in records.snapshots):
            raise ValueError("Candidate payload dimensions mismatch")
        private_input = context + b"".join(s.payload for s in records.snapshots)
    _claim(request, actor)
    try:
        output = matching_backend.run_local_party(job, private_input)
    except matching_backend.MatchingBackendError:
        if job.party == 0:
            raise
        result = MatchingResult(
            metadata=request.metadata,
            source=request.source,
            outcome=MatchingOutcome.ABORTED_OR_INCOMPLETE,
        )
    else:
        records.validate(request)
        validate_matching_request(request, policy, require_epoch=True)
        if job.party == 0:
            if output.outcome != "complete":
                raise matching_backend.MatchingBackendError("Invalid source completion")
            PatientMatchingSession.objects.filter(
                pk=request.metadata.session_id,
                execution_started=True,
                consumed=False,
            ).update(worker_succeeded=True)
            return None
        outcomes = {
            "no_match": MatchingOutcome.NO_MATCH,
            "single_match": MatchingOutcome.SINGLE_MATCH,
            "ambiguous_matches": MatchingOutcome.AMBIGUOUS_MATCHES,
            "key_epoch_invalid": MatchingOutcome.KEY_EPOCH_UNAVAILABLE,
            "context_invalid": MatchingOutcome.ABORTED_OR_INCOMPLETE,
        }
        if output.outcome not in outcomes:
            raise matching_backend.MatchingBackendError("Invalid candidate completion")
        matches = ()
        if output.outcome == "single_match":
            index = output.candidate_index
            if index is None or not 0 <= index < len(request.candidates):
                raise matching_backend.MatchingBackendError("Invalid candidate index")
            matches = (request.candidates[index],)
        result = MatchingResult(
            metadata=request.metadata,
            source=request.source,
            outcome=outcomes[output.outcome],
            matches=matches,
        )
    record_completion(result, worker=worker, policy=policy, records=records)
    return result


@transaction.atomic
def countersign_source_result(
    result: MatchingResult,
    *,
    receiver_signature: MatchingSignature,
    worker: MatchingOperator,
    policy: MatchingPolicy,
    records: SourceMatchingRecords,
    key_id: str,
    private_key: Ed25519PrivateKey,
) -> MatchingSignature:
    """Authenticate a peer assertion after successful local source execution.

    Both parties learn the released outcome at this step. Do not use this as a
    proof against a receiver that lies about its privately revealed output.
    """
    authorize_operator(worker, "execute")
    if policy.local_centre != result.metadata.source_centre:
        raise ValueError("Source countersignature requires source policy")
    session = PatientMatchingSession.objects.select_for_update().get(
        pk=result.metadata.session_id
    )
    if not session.execution_started or not session.worker_succeeded:
        raise ValueError("No successful local source worker completion")
    signature = sign_result(
        result, centre=policy.local_centre, key_id=key_id, private_key=private_key
    )
    verify_signatures(result, (signature, receiver_signature), policy)
    record_completion(result, worker=worker, policy=policy, records=records)
    return signature
