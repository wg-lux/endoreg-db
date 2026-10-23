"""Explicit local acceptance of authenticated study transfer evidence."""

from django.contrib.auth.models import User
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import QuerySet
from lx_dtypes.models.contracts.hub_transfer import HubTransferStudyResourceRowsPayload

from endoreg_db.models.hub.transfer_job import TransferJob
from endoreg_db.models.medical.patient.patient_examination import PatientExamination
from endoreg_db.models.other.study_collection_entry import StudyCollectionEntry
from endoreg_db.services.studies.exchange import import_study_exchange


@transaction.atomic
def accept_study_transfer(
    *,
    transfer_job: TransferJob,
    owner: User,
    examination_scope: QuerySet[PatientExamination],
) -> list[StudyCollectionEntry]:
    """Adopt evidence into the caller's scope; never copy sender access rights."""
    transfer_job = TransferJob.objects.select_for_update().get(pk=transfer_job.pk)
    if (
        transfer_job.resource_kind != TransferJob.ResourceKind.STUDY
        or transfer_job.transfer_status != TransferJob.TransferStatus.APPLIED
        or transfer_job.source_center_id is None
        or not transfer_job.source_node.is_active
        or transfer_job.source_node.owning_center_id != transfer_job.source_center_id
    ):
        raise PermissionDenied("Study transfer source or archive state is invalid")
    transfer_job.clean()
    rows = HubTransferStudyResourceRowsPayload.model_validate(
        transfer_job.resource_rows
    )
    if not rows.study_exchange.collections:
        # No examination exists to prove this caller's center access. Definition
        # packages use the separately authorized package/study authoring workflow.
        raise PermissionDenied("Study transfer acceptance requires scoped observations")
    return import_study_exchange(
        owner=owner,
        examination_scope=examination_scope.filter(
            patient__center_id=transfer_job.source_center_id
        ),
        bundle=rows.study_exchange,
    )
