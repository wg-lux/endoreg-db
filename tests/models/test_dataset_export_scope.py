from __future__ import annotations

from uuid import uuid4

import pytest
from django.test.utils import override_settings
from pydantic import ValidationError as PydanticValidationError

from endoreg_db.models import AIDataSet, Center
from endoreg_db.services.aidataset_exports import (
    AIDataSetExportPayload,
    build_export_payload,
    export_to_standardized_structure,
)


def _dataset() -> AIDataSet:
    return AIDataSet.objects.create(
        name=f"dataset-scope-{uuid4().hex[:8]}",
        dataset_type=AIDataSet.DATASET_TYPE_IMAGE,
        ai_model_type=AIDataSet.AI_MODEL_TYPE_IMAGE_MULTILABEL,
    )


@pytest.mark.django_db
def test_dataset_export_rejects_conflicting_scope_at_service_boundary():
    dataset = _dataset()
    center = Center.objects.create(name=f"scope-center-{uuid4().hex[:8]}")

    with pytest.raises(ValueError, match="center_key or all_centers"):
        export_to_standardized_structure(
            dataset,
            center_key=center.center_key,
            all_centers=True,
            only_validated=True,
        )


@pytest.mark.django_db
def test_dataset_export_rejects_unscoped_local_study_server_export():
    dataset = _dataset()

    with override_settings(ENDOREG_DEPLOYMENT_ROLE="local_study_server"):
        with pytest.raises(ValueError, match="exactly one center scope"):
            export_to_standardized_structure(dataset, only_validated=True)


@pytest.mark.django_db
def test_dataset_export_rejects_unvalidated_local_study_server_export():
    dataset = _dataset()
    center = Center.objects.create(name=f"scope-center-{uuid4().hex[:8]}")

    with override_settings(ENDOREG_DEPLOYMENT_ROLE="local_study_server"):
        with pytest.raises(ValueError, match="only_validated=true"):
            export_to_standardized_structure(
                dataset,
                center_key=center.center_key,
                only_validated=False,
            )


@pytest.mark.django_db
def test_dataset_export_contract_rejects_unknown_fields():
    payload = build_export_payload(_dataset())
    untrusted_payload = {
        **payload.model_dump(mode="python"),
        "protected_internal_note": "must not pass the export boundary",
    }

    with pytest.raises(PydanticValidationError, match="Extra inputs are not permitted"):
        AIDataSetExportPayload.model_validate(untrusted_payload)
