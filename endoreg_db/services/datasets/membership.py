"""Clinical admission at the explicit dataset video membership boundary."""

from django.db import router
from django.db.models.signals import m2m_changed
from django.dispatch import receiver

from endoreg_db.models import AIDataSet, VideoFile
from endoreg_db.models.state.clinical_workflow import ClinicalWorkflowRecord
from endoreg_db.services.clinical_lifecycle.persistence import (
    advance_workflow_if_journaled,
)
from endoreg_db.services.clinical_lifecycle.state_machine import (
    ClinicalLifecycleEvent,
    ClinicalRecordKind,
)


@receiver(m2m_changed, sender=AIDataSet.videos.through)
def admit_video_membership(
    *,
    instance: AIDataSet | VideoFile,
    action: str,
    reverse: bool,
    pk_set: set[int] | None,
    using: str,
    **kwargs: object,
) -> None:
    """The related manager holds the transaction across admission and insertion."""
    if action != "pre_add" or not pk_set:
        return
    if using != router.db_for_write(ClinicalWorkflowRecord):
        raise ValueError(
            "Clinical dataset membership requires the media write database"
        )
    ids = {int(instance.pk)} if reverse else pk_set
    kind = ClinicalRecordKind.VIDEO
    event = ClinicalLifecycleEvent.DATASET_INGESTED
    for video_id in sorted(ids):
        advance_workflow_if_journaled(kind, video_id, event)
