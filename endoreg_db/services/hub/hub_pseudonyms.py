"""Hub-scoped record pseudonyms; site-local UUIDs and identity hashes never leave the site."""

from __future__ import annotations

from uuid import UUID

from lx_dtypes.models.contracts.hub_transfer import HubTransferSensitiveMetaPayload
from lx_dtypes.models.contracts.study_collection import StudyCollectionRecord
from lx_dtypes.models.contracts.study_exchange import (
    StudyExchangeBundle,
    StudyExchangePatientLink,
)
from lx_dtypes.study_exchange import parse_study_exchange

from endoreg_db.utils.hashs import get_hub_record_pseudonym


def hub_identity_payload(
    *, patient_uuid: UUID, examination_uuid: UUID, hub_node_key: str
) -> HubTransferSensitiveMetaPayload:
    """Derive the contract 6.0 identity for one local record and one target hub."""
    return HubTransferSensitiveMetaPayload(
        patient_uuid=get_hub_record_pseudonym(
            patient_uuid, kind="patient", hub_node_key=hub_node_key
        ),
        examination_uuid=get_hub_record_pseudonym(
            examination_uuid, kind="examination", hub_node_key=hub_node_key
        ),
    )


def hub_scoped_study_exchange(
    bundle: StudyExchangeBundle, *, hub_node_key: str
) -> StudyExchangeBundle:
    """Replace every local patient/examination UUID with the target hub's pseudonym.

    Uses the same derivation as media transfers, so hub-side study import finds
    the pseudonymous rows those transfers created.
    """
    bundle = parse_study_exchange(bundle)
    examinations: dict[UUID, UUID] = {}
    patients: dict[UUID, UUID] = {}
    for link in bundle.patients:
        examinations[link.patient_examination_uuid] = get_hub_record_pseudonym(
            link.patient_examination_uuid, kind="examination", hub_node_key=hub_node_key
        )
        patients[link.patient_uuid] = get_hub_record_pseudonym(
            link.patient_uuid, kind="patient", hub_node_key=hub_node_key
        )
    collections: list[StudyCollectionRecord] = []
    for item in bundle.collections:
        record = item.record.model_copy(
            update={"patient": str(patients[UUID(item.record.patient)])}
        )
        collections.append(
            item.model_copy(
                update={
                    "patient_examination_uuid": examinations[
                        item.patient_examination_uuid
                    ],
                    "record": record,
                }
            )
        )
    # Reparse so closure and evaluation checks run on the pseudonymous bundle.
    return parse_study_exchange(
        bundle.model_copy(
            update={
                "collections": collections,
                "patients": [
                    StudyExchangePatientLink(
                        patient_examination_uuid=examinations[
                            link.patient_examination_uuid
                        ],
                        patient_uuid=patients[link.patient_uuid],
                    )
                    for link in bundle.patients
                ],
            }
        )
    )
