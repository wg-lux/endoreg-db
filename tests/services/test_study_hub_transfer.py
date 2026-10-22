"""Standalone study transfer preserves source evidence without assigning ownership."""

from uuid import uuid4

import pytest
from django.contrib.auth.models import User
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import override_settings
from lx_dtypes.models.contracts.study import StudyDefinition
from lx_dtypes.models.contracts.study_exchange import (
    StudyExchangeBundle,
    StudyExchangeStudy,
)
from lx_dtypes.study_exchange import study_exchange_digest
from rest_framework.test import APIRequestFactory

from endoreg_db.models.administration.center.center import Center
from endoreg_db.models.hub.network_node import NetworkNode
from endoreg_db.models.hub.transfer_job import TransferJob
from endoreg_db.models.other.study import Study
from endoreg_db.models.other.study_collection_entry import StudyCollectionEntry
from endoreg_db.models.medical.patient.patient_examination import PatientExamination
from endoreg_db.models.administration.person.patient.patient import Patient
from endoreg_db.services.hub.hub_pseudonyms import hub_scoped_study_exchange
from endoreg_db.services.hub.study_transfer import accept_study_transfer
from endoreg_db.services.hub.transfers import apply_transfer_metadata
from endoreg_db.services.studies.exchange import export_study_exchange
from tests.services.test_study_exchange import evidence as evidence
from endoreg_db.serializers.hub.transfer_job import TransferJobCreateSerializer
from endoreg_db.views.media.hub.transfers import HubTransferCreateView

pytestmark = pytest.mark.django_db


@pytest.fixture
def nodes() -> tuple[Center, NetworkNode, NetworkNode]:
    center = Center.objects.create(name="study-hub", display_name="Study Hub")
    source = NetworkNode.objects.create(
        node_key="study-source",
        display_name="Source",
        owning_center=center,
        role=NetworkNode.Role.SITE_NODE,
    )
    source.set_shared_secret("synthetic-study-transfer-secret")
    source.save()
    target = NetworkNode.objects.create(
        node_key="study-target",
        display_name="Target",
        role=NetworkNode.Role.CENTRAL_HUB,
    )
    return center, source, target


def _payload(
    nodes: tuple[Center, NetworkNode, NetworkNode],
    bundle: StudyExchangeBundle | None = None,
) -> dict[str, object]:
    center, source, target = nodes
    bundle = bundle or StudyExchangeBundle(
        studies=[
            StudyExchangeStudy(
                study_uuid=uuid4(),
                definition=StudyDefinition(name="Synthetic research"),
            )
        ]
    )
    digest = study_exchange_digest(bundle)
    return {
        "transfer_key": f"{source.node_key}__study__{digest}__metadata_v1",
        "source_node_key": source.node_key,
        "target_node_key": target.node_key,
        "source_center_key": center.center_key,
        "resource_kind": "study",
        "resource_hash": digest,
        "transfer_mode": "metadata_only",
        "processing_policy": "ingest_only_no_processing",
        "processing_intent": "sender_requests_archive_only",
        "cleanup_policy": "retain_all",
        "payload_schema_version": "6.0",
        "resource_rows": {"study_exchange": bundle.model_dump(mode="json")},
        "processing_snapshot": {"sender_processing_success": True},
    }


@override_settings(
    ENDOREG_DEPLOYMENT_ROLE="central_hub",
    ENDOREG_ENABLE_HUB_TRANSFERS=True,
    ENDOREG_HUB_TRANSFER_REQUIRE_MTLS=False,
)
def test_study_archive_endpoint_and_replay(
    nodes: tuple[Center, NetworkNode, NetworkNode],
) -> None:
    payload = _payload(nodes)
    factory = APIRequestFactory()
    for expected in (201, 200):
        request = factory.post(
            "/media/hub/transfers/",
            payload,
            format="json",
            secure=True,
            HTTP_X_NETWORK_NODE_KEY=nodes[1].node_key,
            HTTP_X_NETWORK_NODE_SECRET="synthetic-study-transfer-secret",
        )
        response = HubTransferCreateView.as_view()(request)
        assert response.status_code == expected, response.data
    receipt = TransferJob.objects.get(resource_kind="study")
    assert receipt.transfer_status == "applied"
    assert "acceptance required" in receipt.status_detail
    assert receipt.target_object_id is None
    assert receipt.created_by is None
    assert Study.objects.count() == 0
    assert receipt.resource_rows == payload["resource_rows"]
    receipt.resource_hash = "0" * 64
    with pytest.raises(ValidationError):
        receipt.save()


@pytest.mark.parametrize(
    "field,value",
    [
        ("resource_hash", "0" * 64),
        ("transfer_mode", "metadata_and_processed_media"),
        ("processing_policy", "reprocess_always"),
        ("cleanup_policy", "delete_central_raw_after_apply"),
        ("transfer_key", "different-key"),
    ],
)
def test_study_contract_rejects_tampering(
    nodes: tuple[Center, NetworkNode, NetworkNode], field: str, value: str
) -> None:
    payload = _payload(nodes)
    payload[field] = value
    serializer = TransferJobCreateSerializer(data=payload)
    assert not serializer.is_valid()


@override_settings(
    ENDOREG_DEPLOYMENT_ROLE="central_hub",
    ENDOREG_ENABLE_HUB_TRANSFERS=True,
    ENDOREG_HUB_TRANSFER_REQUIRE_MTLS=False,
)
def test_study_archive_rejects_wrong_credentials(
    nodes: tuple[Center, NetworkNode, NetworkNode],
) -> None:
    request = APIRequestFactory().post(
        "/media/hub/transfers/",
        _payload(nodes),
        format="json",
        secure=True,
        HTTP_X_NETWORK_NODE_KEY=nodes[1].node_key,
        HTTP_X_NETWORK_NODE_SECRET="wrong-secret",
    )
    response = HubTransferCreateView.as_view()(request)
    assert response.status_code == 403
    assert not TransferJob.objects.exists()


def test_explicit_acceptance_is_scoped_and_idempotent(
    nodes: tuple[Center, NetworkNode, NetworkNode],
    evidence: tuple[User, Study, PatientExamination],
) -> None:
    owner, study, examination = evidence
    center, source, target = nodes
    examination.patient.center = center
    examination.patient.save()
    scope = PatientExamination.objects.filter(pk=examination.pk)
    local_bundle = export_study_exchange(
        owner=owner, study=study, examination_scope=scope
    )
    # The site-local bundle is rejected; only hub pseudonyms may leave the site.
    assert not TransferJobCreateSerializer(
        data=_payload(nodes, local_bundle)
    ).is_valid()
    bundle = hub_scoped_study_exchange(local_bundle, hub_node_key=target.node_key)
    link = bundle.patients[0]
    assert link.patient_uuid != examination.patient.uuid
    assert link.patient_examination_uuid != examination.uuid
    # Simulate the separate hub: it holds no site collection entries, and media
    # transfers created its rows under the same pseudonyms.
    StudyCollectionEntry.objects.filter(study=study).delete()
    Patient.objects.filter(pk=examination.patient_id).update(uuid=link.patient_uuid)
    PatientExamination.objects.filter(pk=examination.pk).update(
        uuid=link.patient_examination_uuid
    )
    examination.refresh_from_db()
    payload = _payload(nodes, bundle)
    for key in ("source_node_key", "target_node_key", "source_center_key"):
        payload.pop(key)
    receipt = TransferJob.objects.create(
        source_node=source, target_node=target, source_center=center, **payload
    )
    apply_transfer_metadata(receipt)
    assert (
        len(
            accept_study_transfer(
                transfer_job=receipt, owner=owner, examination_scope=scope
            )
        )
        == 1
    )
    assert (
        len(
            accept_study_transfer(
                transfer_job=receipt, owner=owner, examination_scope=scope
            )
        )
        == 1
    )
    with pytest.raises(PermissionDenied):
        accept_study_transfer(
            transfer_job=receipt,
            owner=owner,
            examination_scope=PatientExamination.objects.none(),
        )
    other_center = Center.objects.create(
        name="other-study-center", display_name="Other"
    )
    examination.patient.center = other_center
    examination.patient.save()
    with pytest.raises(PermissionDenied):
        accept_study_transfer(
            transfer_job=receipt, owner=owner, examination_scope=scope
        )


@override_settings(
    ENDOREG_DEPLOYMENT_ROLE="central_hub",
    ENDOREG_ENABLE_HUB_TRANSFERS=True,
    ENDOREG_HUB_TRANSFER_REQUIRE_MTLS=True,
    ENDOREG_HUB_TRANSFER_MTLS_META_KEY="HTTP_X_VERIFIED_CLIENT",
    ENDOREG_HUB_TRANSFER_MTLS_META_VALUE="SUCCESS",
)
def test_study_archive_requires_configured_mtls(
    nodes: tuple[Center, NetworkNode, NetworkNode],
) -> None:
    request = APIRequestFactory().post(
        "/media/hub/transfers/",
        _payload(nodes),
        format="json",
        secure=True,
        HTTP_X_NETWORK_NODE_KEY=nodes[1].node_key,
        HTTP_X_NETWORK_NODE_SECRET="synthetic-study-transfer-secret",
    )
    response = HubTransferCreateView.as_view()(request)
    assert response.status_code == 403
    assert not TransferJob.objects.exists()
