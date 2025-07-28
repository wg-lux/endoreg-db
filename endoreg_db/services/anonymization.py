# endoreg_db/services/anonymization.py
from django.db import transaction
from endoreg_db.models import VideoFile, RawPdfFile
from endoreg_db.services.video_import import import_and_anonymize   # existing func

class AnonymizationService:
    """
    Orchestrates long‑running anonymization tasks so the view only
    does HTTP <-> Service translation.
    """

    # ---------- READ ----------------------------------------------------
    @staticmethod
    def get_status(file_id: int):
        """
        Retrieve the anonymization status and media type for a file by its ID.
        
        Returns:
            dict or None: A dictionary containing the file's media type and anonymization status if found, or None if no matching file exists.
        """
        vf = VideoFile.objects.select_related("state", "sensitive_meta").filter(pk=file_id).first()
        if vf:
            return {
                "mediaType": "video",
                "anonymizationStatus": vf.state.anonymization_status,
            }

        pdf = RawPdfFile.objects.select_related("sensitive_meta").filter(pk=file_id).first()
        if pdf:
            return {
                "mediaType": "pdf",
                "status": pdf.state.anonymization_status,
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
        if pdf and pdf.sensitive_meta:
            pdf.sensitive_meta.anonymization_validated = True
            pdf.sensitive_meta.save(update_fields=["anonymization_validated"])
            return "pdf"

        return None
    
    @staticmethod
    def list_items():
        """
        Retrieve a combined list of all video and PDF files with their anonymization statuses and timestamps.
        
        Returns:
            list: A list of dictionaries, each containing the file's ID, media type, anonymization status, creation date, and last modification date.
        """
        video_files = VideoFile.objects.select_related("state").all()
        pdf_files = RawPdfFile.objects.select_related("sensitive_meta").all()

        data = []
        for vf in video_files:
            data.append({
                "id": vf.id,
                "mediaType": "video",
                "anonymizationStatus": vf.state.anonymization_status,
                "createdAt": vf.date_created,
                "updatedAt": vf.date_modified,
            })

        for pdf in pdf_files:
            data.append({
                "id": pdf.id,
                "mediaType": "pdf",
                "anonymizationStatus": pdf.state.anonymization_status,
                "createdAt": pdf.date_created,
                "updatedAt": pdf.date_modified,
            })

        return data
