from django.contrib.contenttypes.models import ContentType
from endoreg_db.models.media.storage.processing_history import ProcessingHistory

def _record_history(self, instance, state, message: str = "") -> None:
    ProcessingHistory.objects.create(
        content_type=ContentType.objects.get_for_model(instance.__class__),
        object_id=instance.pk,
        file_name=getattr(instance, "file").name if hasattr(instance, "file") else "",
        state=state.anonymization_status,
        message=message,
    )
