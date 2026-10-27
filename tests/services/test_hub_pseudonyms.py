"""Hub records carry site-salted pseudonyms; local UUIDs and hashes stay on the site."""

from datetime import date
from uuid import UUID, uuid4

import pytest
from django.contrib.auth.models import User
from lx_dtypes.models.contracts.hub_transfer import HubTransferSensitiveMetaPayload
from pydantic import ValidationError

from endoreg_db.models.medical.patient.patient_examination import PatientExamination
from endoreg_db.models.other.study import Study
from endoreg_db.services.hub.hub_pseudonyms import (
    hub_identity_payload,
    hub_scoped_study_exchange,
)
from endoreg_db.services.studies.exchange import export_study_exchange
from endoreg_db.utils.hashs import (
    HubRecordKind,
    get_hub_record_pseudonym,
    get_patient_identity_fingerprint,
    get_patient_uuid,
)
from tests.services.test_study_exchange import evidence as evidence

SALT_A = "9f3c1a7e5b2d4086c1e3a5f7092b4d6e8a0c2e4f6183a5c7e9b1d3f5a7092c4e"
SALT_B = "3e8d1b6f0a4c2e7d9b5f1a3c6e8d0b2f4a7c9e1d3b5f8a0c2e4d6b9f1a3c5e7d"
LOCAL = UUID("1b4e28ba-2fa1-41d2-883f-0016d3cca427")


@pytest.mark.parametrize("kind", ["patient", "examination"])
@pytest.mark.parametrize("hub", ["hub-a", " hub-a ", "hub-b"])
def test_pseudonym_is_deterministic_uuidv8_and_differs_from_local_uuid(
    kind: HubRecordKind, hub: str
) -> None:
    first = get_hub_record_pseudonym(LOCAL, kind=kind, hub_node_key=hub, salt=SALT_A)
    again = get_hub_record_pseudonym(LOCAL, kind=kind, hub_node_key=hub, salt=SALT_A)
    assert first == again
    assert first.version == 8
    assert first != LOCAL
    assert LOCAL.hex not in first.hex


@pytest.mark.no_db
@pytest.mark.parametrize(
    "random_local", [False, True], ids=["current", "stored-random"]
)
@pytest.mark.parametrize("center", ["center-a", "center-b"])
@pytest.mark.parametrize("salt", [SALT_A, SALT_B], ids=["active", "retained"])
def test_all_keys_exposed_demographic_attack_and_mapping_loss(
    random_local: bool, center: str, salt: str
) -> None:
    """Finite attack comparison, not a proof of anonymity or a deployed alternative."""
    candidates = ("Synthetic Alice", "Synthetic Bob")
    # Both centre/generation keys are available, unlike the historical one-key trial.
    guesses: dict[UUID, str] = {}
    for key in (SALT_A, SALT_B):
        for scope in ("center-a", "center-b"):
            for name in candidates:
                fingerprint = get_patient_identity_fingerprint(
                    first_name=name,
                    last_name="Synthetic",
                    dob=date(1980, 1, 2),
                    center_key=scope,
                    salt=key,
                )
                local = get_patient_uuid(fingerprint, center_key=scope, salt=key)
                guesses[
                    get_hub_record_pseudonym(
                        local,
                        kind="patient",
                        hub_node_key="hub-a",
                        salt=key,
                    )
                ] = name

    fingerprint = get_patient_identity_fingerprint(
        first_name=candidates[0],
        last_name="Synthetic",
        dob=date(1980, 1, 2),
        center_key=center,
        salt=salt,
    )
    local = (
        uuid4()
        if random_local
        else get_patient_uuid(
            fingerprint,
            center_key=center,
            salt=salt,
        )
    )
    published = get_hub_record_pseudonym(
        local,
        kind="patient",
        hub_node_key="hub-a",
        salt=salt,
    )
    assert guesses.get(published) == (None if random_local else candidates[0])
    # Reusing the stored resolver result preserves two findings in one case.
    followup = get_hub_record_pseudonym(
        local,
        kind="patient",
        hub_node_key="hub-a",
        salt=salt,
    )
    assert followup == published
    # Scenario B: leaked source mapping defeats either identifier construction.
    source_mapping = {local: candidates[0]}
    recovered = {
        get_hub_record_pseudonym(
            record,
            kind="patient",
            hub_node_key="hub-a",
            salt=salt,
        ): person
        for record, person in source_mapping.items()
    }
    assert recovered[published] == recovered[followup] == candidates[0]


@pytest.mark.no_db
def test_random_case_mapping_limits_linkage_but_known_label_propagates() -> None:
    """Test-only storage alternative: one persisted random token per approved scope."""
    scopes = ("study-a/case-1", "study-a/case-2", "study-b/case-1")
    # No derivation from demographics or keys; this table must remain source-local.
    mapping = {scope: uuid4() for scope in scopes}
    first, followup = mapping[scopes[0]], mapping[scopes[0]]
    assert first == followup
    assert len(set(mapping.values())) == len(scopes)
    known_label = {first: "Synthetic Alice"}
    assert known_label[followup] == "Synthetic Alice"
    assert mapping[scopes[1]] not in known_label
    assert mapping[scopes[2]] not in known_label
    # Unavailable mapping must fail, never create a replacement identity on read.
    with pytest.raises(KeyError):
        _ = mapping["missing-case"]


@pytest.mark.no_db
def test_encrypted_mapping_is_readable_with_compromised_key() -> None:
    from cryptography.fernet import Fernet

    key = Fernet.generate_key()
    original = b'{"case-token":"Synthetic Alice"}'
    backup = Fernet(key).encrypt(original)
    assert Fernet(key).decrypt(backup) == original


@pytest.mark.parametrize(
    "record,kind,hub,salt",
    [
        (LOCAL, "patient", "hub-a", SALT_B),
        (LOCAL, "patient", "hub-b", SALT_A),
        (LOCAL, "examination", "hub-a", SALT_A),
        (UUID("1b4e28ba-2fa1-41d2-883f-0016d3cca428"), "patient", "hub-a", SALT_A),
    ],
)
def test_pseudonym_separates_salt_hub_kind_and_record(
    record: UUID, kind: HubRecordKind, hub: str, salt: str
) -> None:
    base = get_hub_record_pseudonym(
        LOCAL, kind="patient", hub_node_key="hub-a", salt=SALT_A
    )
    assert base != get_hub_record_pseudonym(
        record, kind=kind, hub_node_key=hub, salt=salt
    )


@pytest.mark.parametrize("hub", ["", "   "])
def test_pseudonym_requires_target_hub(hub: str) -> None:
    with pytest.raises(ValueError, match="target hub node key"):
        get_hub_record_pseudonym(LOCAL, kind="patient", hub_node_key=hub, salt=SALT_A)


def test_identity_payload_passes_contract_while_local_uuid_is_rejected() -> None:
    examination = UUID("1b4e28ba-2fa1-41d2-883f-0016d3cca429")
    payload = hub_identity_payload(
        patient_uuid=LOCAL, examination_uuid=examination, hub_node_key="hub-a"
    )
    assert payload.patient_uuid == get_hub_record_pseudonym(
        LOCAL, kind="patient", hub_node_key="hub-a"
    )
    assert payload.examination_uuid == get_hub_record_pseudonym(
        examination, kind="examination", hub_node_key="hub-a"
    )
    with pytest.raises(ValidationError, match="UUIDv8 pseudonyms"):
        HubTransferSensitiveMetaPayload.model_validate(
            {"patient_uuid": str(LOCAL), "examination_uuid": str(examination)}
        )


@pytest.mark.django_db
def test_study_bundle_uses_media_pseudonyms_and_keeps_evaluation(
    evidence: tuple[User, Study, PatientExamination],
) -> None:
    owner, study, examination = evidence
    local = export_study_exchange(
        owner=owner,
        study=study,
        examination_scope=PatientExamination.objects.filter(pk=examination.pk),
    )
    scoped = hub_scoped_study_exchange(local, hub_node_key="hub-a")
    identity = hub_identity_payload(
        patient_uuid=examination.patient.uuid,
        examination_uuid=examination.uuid,
        hub_node_key="hub-a",
    )
    link = scoped.patients[0]
    assert (link.patient_uuid, link.patient_examination_uuid) == (
        identity.patient_uuid,
        identity.examination_uuid,
    )
    item = scoped.collections[0]
    assert item.patient_examination_uuid == identity.examination_uuid
    assert item.record.patient == str(identity.patient_uuid)
    assert item.evaluation == local.collections[0].evaluation
    serialized = scoped.model_dump_json()
    assert str(examination.uuid) not in serialized
    assert str(examination.patient.uuid) not in serialized
    assert hub_scoped_study_exchange(local, hub_node_key="hub-b").patients != (
        scoped.patients
    )
