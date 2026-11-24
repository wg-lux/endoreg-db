"""Real database bindings around a controlled worker boundary; native 2PC is separate."""

from dataclasses import replace
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from django.db import connection
from datetime import timedelta
from uuid import uuid4

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from django.contrib.auth.models import User
from django.utils import timezone

from endoreg_db.config.identity_hashing import identity_keyring_snapshot
from endoreg_db.config.secret_keyring import SecretKeyring
from endoreg_db.models import Center, Patient, SensitiveMeta, SensitiveMetaState
from endoreg_db.models.hub.patient_matching import (
    PatientMatchingSession,
    PatientMatchingRelationship,
)
from endoreg_db.schemas.patient_matching import (
    MatchingMetadata,
    MatchingRequest,
    MatchingResult,
    MatchingOutcome,
)
from endoreg_db.services.patient_identity.matching.matching import (
    CandidateMatchingRecords,
    SourceMatchingRecords,
    run_matching_worker,
    countersign_source_result,
)
from endoreg_db.services.patient_identity.matching.matching_acceptance import (
    MatchingPolicy,
    MatchingTrustedKey,
    register_request,
    accept_result,
    sign_result,
)
from endoreg_db.services.patient_identity.matching.matching_backend import (
    BackendJob,
    BackendResult,
    MatchingBackendError,
)
from endoreg_db.services.patient_identity.matching import matching_backend
from endoreg_db.services.patient_identity.matching.matching_inputs import (
    prepare_source_input,
    prepare_candidate_input,
    MatchingInputError,
)
from endoreg_db.utils.hashs import get_identity_salt_fingerprint, get_bytes_hash
from tests.services.test_patient_matching_inputs import (
    identity as identity,
    KEY,
    NEXT_KEY,
    authorization,
)

pytestmark = pytest.mark.django_db
CIRCUIT = "a" * 64


@pytest.fixture
def setup(
    identity: SensitiveMeta,
) -> tuple[
    MatchingRequest,
    SourceMatchingRecords,
    CandidateMatchingRecords,
    User,
    MatchingPolicy,
    tuple[Ed25519PrivateKey, Ed25519PrivateKey],
]:
    source = prepare_source_input(
        sensitive_meta_id=identity.pk,
        authorization=authorization(identity),
        allowed_key_epochs=frozenset({get_identity_salt_fingerprint(KEY)}),
    )
    token = identity_keyring_snapshot.set(SecretKeyring(NEXT_KEY))
    try:
        centre = Center.objects.create(
            name="candidate-centre", center_key="candidate-centre"
        )
        row = SensitiveMeta.objects.create(
            center=centre,
            patient_first_name=identity.patient_first_name,
            patient_last_name=identity.patient_last_name,
            patient_dob=identity.patient_dob,
            examination_date=identity.examination_date,
        )
        SensitiveMetaState.objects.update_or_create(
            origin=row, defaults={"names_verified": True, "dob_verified": True}
        )
        candidate = prepare_candidate_input(
            sensitive_meta_id=row.pk,
            authorization=authorization(row),
            source_center_key=source.reference.centre,
        )
    finally:
        identity_keyring_snapshot.reset(token)
    source_records = SourceMatchingRecords(
        source, authorization(identity), frozenset({source.reference.key_epoch})
    )
    candidate_records = CandidateMatchingRecords((candidate,), authorization(row))
    request = MatchingRequest(
        metadata=MatchingMetadata(
            session_id=uuid4(),
            source_centre=source.reference.centre,
            receiver_centre=candidate.reference.centre,
            audience="local",
            purpose="review",
            scheme="patient-identity-v1",
            key_epoch=source.reference.key_epoch,
            circuit=CIRCUIT,
            batch_ref=uuid4(),
            expires_at=timezone.now() + timedelta(minutes=5),
        ),
        source=source.reference,
        candidates=(candidate.reference,),
    )
    operator = User.objects.create_superuser(username="matching-worker", password=None)
    keys = Ed25519PrivateKey.generate(), Ed25519PrivateKey.generate()
    policy = MatchingPolicy(
        local_centre=candidate.reference.centre,
        audience="local",
        purpose="review",
        scheme="patient-identity-v1",
        circuit=CIRCUIT,
        available_epochs=frozenset({source.reference.key_epoch}),
        max_source_requests=2,
        max_candidates=10,
        trusted_keys=tuple(
            MatchingTrustedKey(centre, "key-1", key.public_key().public_bytes_raw())
            for centre, key in zip(
                (source.reference.centre, candidate.reference.centre), keys, strict=True
            )
        ),
    )
    return request, source_records, candidate_records, operator, policy, keys


def job_for(
    request: MatchingRequest, records: CandidateMatchingRecords, *, source: bool = False
) -> BackendJob:
    return BackendJob(
        party=0 if source else 1,
        runtime_directory="synthetic-unused",
        program="matching",
        key_size=len(KEY),
        payload_sizes=tuple(len(s.payload) for s in records.snapshots),
        public_epoch=request.metadata.key_epoch,
        peer_ip="127.0.0.1",
        port=19234,
        artifacts=(("Programs/Source/patient_matching_resolver.mpc", CIRCUIT),),
    )


@pytest.mark.parametrize(
    "outcome",
    ["single_match", "no_match", "ambiguous_matches", "key_epoch_invalid", "failure"],
)
def test_receiver_records_actual_worker_outcome_without_merging(
    setup: tuple[
        MatchingRequest,
        SourceMatchingRecords,
        CandidateMatchingRecords,
        User,
        MatchingPolicy,
        tuple[Ed25519PrivateKey, Ed25519PrivateKey],
    ],
    monkeypatch: pytest.MonkeyPatch,
    outcome: str,
) -> None:
    request, _, records, operator, policy, keys = setup
    before = list(Patient.objects.values_list("uuid", "patient_hash"))
    register_request(request, operator=operator, policy=policy, records=records)
    job = job_for(request, records)

    def worker(received: BackendJob, data: bytes) -> BackendResult:
        assert received == job
        assert data[32:] == b"".join(s.payload for s in records.snapshots)
        assert data[:32].hex() == get_bytes_hash(
            b"endoreg-patient-matching-request-v1\n"
            + request.model_dump_json().encode("utf-8")
        )
        assert KEY not in data
        if outcome == "failure":
            raise MatchingBackendError("Backend deadline exceeded")
        result = matching_backend.parse_backend_output(
            {
                "single_match": b"LX_MATCH_RESULT=1",
                "no_match": b"LX_MATCH_RESULT=0",
                "ambiguous_matches": b"LX_MATCH_RESULT=2",
                "key_epoch_invalid": b"LX_MATCH_RESULT=3",
            }[outcome],
            party=1,
            candidate_count=1,
        )
        return result

    monkeypatch.setattr(matching_backend, "run_local_party", worker)
    result = run_matching_worker(
        request, job=job, worker=operator, policy=policy, records=records
    )
    assert result is not None
    expected = (
        "aborted_or_incomplete"
        if outcome == "failure"
        else "key_epoch_unavailable"
        if outcome == "key_epoch_invalid"
        else outcome
    )
    assert result.outcome == expected
    signatures = tuple(
        sign_result(result, centre=c, key_id="key-1", private_key=k)
        for c, k in zip(
            (request.metadata.source_centre, request.metadata.receiver_centre),
            keys,
            strict=True,
        )
    )
    link = accept_result(
        result, signatures=signatures, operator=operator, policy=policy, records=records
    )
    assert (link is not None) == (outcome == "single_match")
    assert list(Patient.objects.values_list("uuid", "patient_hash")) == before
    with pytest.raises(ValueError, match="claimed|consumed"):
        run_matching_worker(
            request, job=job, worker=operator, policy=policy, records=records
        )


def test_source_signs_peer_assertion_only_after_local_success(
    setup: tuple[
        MatchingRequest,
        SourceMatchingRecords,
        CandidateMatchingRecords,
        User,
        MatchingPolicy,
        tuple[Ed25519PrivateKey, Ed25519PrivateKey],
    ],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request, source, candidates, operator, policy, keys = setup
    policy = replace(policy, local_centre=request.metadata.source_centre)
    register_request(request, operator=operator, policy=policy, records=source)
    result = MatchingResult(
        metadata=request.metadata,
        source=request.source,
        outcome=MatchingOutcome.SINGLE_MATCH,
        matches=request.candidates,
    )
    peer = sign_result(
        result,
        centre=request.metadata.receiver_centre,
        key_id="key-1",
        private_key=keys[1],
    )
    with pytest.raises(ValueError, match="No successful"):
        countersign_source_result(
            result,
            receiver_signature=peer,
            worker=operator,
            policy=policy,
            records=source,
            key_id="key-1",
            private_key=keys[0],
        )

    def worker(_job: BackendJob, data: bytes) -> BackendResult:
        assert data[:-32] == source.snapshot.key + source.snapshot.target
        assert data[-32:].hex() == get_bytes_hash(
            b"endoreg-patient-matching-request-v1\n"
            + request.model_dump_json().encode("utf-8")
        )
        return BackendResult("complete")

    monkeypatch.setattr(matching_backend, "run_local_party", worker)
    assert (
        run_matching_worker(
            request,
            job=job_for(request, candidates, source=True),
            worker=operator,
            policy=policy,
            records=source,
        )
        is None
    )
    own = countersign_source_result(
        result,
        receiver_signature=peer,
        worker=operator,
        policy=policy,
        records=source,
        key_id="key-1",
        private_key=keys[0],
    )
    assert (
        accept_result(
            result,
            signatures=(own, peer),
            operator=operator,
            policy=policy,
            records=source,
        )
        is not None
    )


def test_changed_candidate_after_worker_cannot_link(
    setup: tuple[
        MatchingRequest,
        SourceMatchingRecords,
        CandidateMatchingRecords,
        User,
        MatchingPolicy,
        tuple[Ed25519PrivateKey, Ed25519PrivateKey],
    ],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request, _, records, operator, policy, _ = setup
    register_request(request, operator=operator, policy=policy, records=records)

    def worker(_job: BackendJob, _data: bytes) -> BackendResult:
        SensitiveMeta.objects.filter(pk=records.snapshots[0].evidence.meta_id).update(
            patient_first_name="Changed"
        )
        return BackendResult("single_match", 0)

    monkeypatch.setattr(matching_backend, "run_local_party", worker)
    with pytest.raises(MatchingInputError):
        run_matching_worker(
            request,
            job=job_for(request, records),
            worker=operator,
            policy=policy,
            records=records,
        )
    assert not PatientMatchingSession.objects.get(
        pk=request.metadata.session_id
    ).completion_json
    assert not PatientMatchingRelationship.objects.exists()


def test_wrong_role_and_circuit_rejected_before_execution(
    setup: tuple[
        MatchingRequest,
        SourceMatchingRecords,
        CandidateMatchingRecords,
        User,
        MatchingPolicy,
        tuple[Ed25519PrivateKey, Ed25519PrivateKey],
    ],
) -> None:
    request, _, records, operator, policy, _ = setup
    register_request(request, operator=operator, policy=policy, records=records)
    job = job_for(request, records)
    for invalid in (
        replace(job, party=0),
        replace(job, public_epoch="0" * 64),
        replace(job, artifacts=()),
    ):
        with pytest.raises(ValueError):
            run_matching_worker(
                request, job=invalid, worker=operator, policy=policy, records=records
            )
    assert not PatientMatchingSession.objects.get(
        pk=request.metadata.session_id
    ).execution_started


@pytest.mark.django_db(transaction=True)
def test_concurrent_workers_claim_once(
    setup: tuple[
        MatchingRequest,
        SourceMatchingRecords,
        CandidateMatchingRecords,
        User,
        MatchingPolicy,
        tuple[Ed25519PrivateKey, Ed25519PrivateKey],
    ],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request, _, records, operator, policy, _ = setup
    register_request(request, operator=operator, policy=policy, records=records)
    gate = Barrier(2)

    def worker(_job: BackendJob, _data: bytes) -> BackendResult:
        return BackendResult("no_match")

    monkeypatch.setattr(matching_backend, "run_local_party", worker)

    def attempt(_index: int) -> str:
        try:
            gate.wait(timeout=10)
            try:
                run_matching_worker(
                    request,
                    job=job_for(request, records),
                    worker=operator,
                    policy=policy,
                    records=records,
                )
                return "completed"
            except ValueError as error:
                assert "claimed" in str(error)
                return "rejected"
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attempt, range(2)))
    assert sorted(results) == ["completed", "rejected"]
