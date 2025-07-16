# endoreg_db/services/anonymization.py
from django.db import transaction
from endoreg_db.models import VideoFile, RawPdfFile, VideoState
from endoreg_db.services.video_import import import_and_anonymize   # existing func

class AnonymizationService:
    """
    Orchestrates long‑running anonymization tasks so the view only
    does HTTP <-> Service translation.
    """

    # ---------- READ ----------------------------------------------------
    @staticmethod
    def get_status(file_id: int):
        vf = VideoFile.objects.select_related("state", "sensitive_meta").filter(pk=file_id).first()
        if vf:
            return {
                "type": "video",
                "status": vf.state.anonymization_status,
            }

        pdf = RawPdfFile.objects.select_related("sensitive_meta").filter(pk=file_id).first()
        if pdf:
            status = "done" if pdf.anonymized_text and pdf.anonymized_text.strip() else "not_started"
            return {
                "type": "pdf",
                "status": status,
            }
        return None

    # ---------- COMMANDS ------------------------------------------------
    @staticmethod
    @transaction.atomic
    def start(file_id: int):
        vf = VideoFile.objects.select_related("state", "sensitive_meta").filter(pk=file_id).first()
        if vf:
            import_and_anonymize(
                vf.get_raw_file_path(),
                vf.center.name if vf.center else None,
                vf.processor.name if vf.processor else None
            )
            return "video"

        pdf = RawPdfFile.objects.select_related("sensitive_meta").filter(pk=file_id).first()
        if pdf and pdf.sensitive_meta:
            pdf.sensitive_meta.anonymization_started = True
            pdf.sensitive_meta.save(update_fields=["anonymization_started"])
            return "pdf"

        return None

    @staticmethod
    @transaction.atomic
    def validate(file_id: int):
        vf = VideoFile.objects.select_related("state").filter(pk=file_id).first()
        if vf:
            vf.state.mark_anonymization_validated()
            return "video"

        pdf = RawPdfFile.objects.select_related("sensitive_meta").filter(pk=file_id).first()
        if pdf:
            pdf.sensitive_meta.anonymization_validated = True
            pdf.sensitive_meta.save(update_fields=["anonymization_validated"])
            return "pdf"

        return None
