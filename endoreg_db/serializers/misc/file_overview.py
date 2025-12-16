from typing import TYPE_CHECKING

from rest_framework import serializers

from endoreg_db.models.media import RawPdfFile, VideoFile
from endoreg_db.models.state.anonymization import AnonymizationState as PdfAnonymizationState
from endoreg_db.models.state.anonymization import AnonymizationState as VideoAnonymizationState

if TYPE_CHECKING:
    pass


class FileOverviewSerializer(serializers.Serializer):
    """
    Polymorphic "union" serializer – we normalise both model types
    (VideoFile, RawPdfFile) into the data structure the Vue store needs.
    """

    # --- fields expected by the front-end ---------------------------
    # All fields are read_only since they're computed in to_representation
    id = serializers.IntegerField(read_only=True)
    filename = serializers.CharField(read_only=True)
    mediaType = serializers.CharField(read_only=True)
    anonymizationStatus = serializers.CharField(read_only=True)
    annotationStatus = serializers.CharField(read_only=True)
    createdAt = serializers.DateTimeField(read_only=True)

    # --- converting DB objects to that shape -----------------------
    def to_representation(self, instance):
        """
        Return a unified dictionary representation of either a VideoFile or RawPdfFile instance for front-end use.

        For VideoFile instances, extracts and structures metadata such as patient, examination, equipment, and examiner information, and generates an anonymized version of the text by replacing sensitive fields with placeholders. For RawPdfFile instances, extracts text and anonymized text directly and determines statuses based on available fields.

        Parameters:
            instance: A VideoFile or RawPdfFile object to be serialized.

        Returns:
            dict: A normalized dictionary containing id, filename, mediaType, anonymizationStatus, annotationStatus, createdAt, text, and anonymizedText fields.

        Raises:
            TypeError: If the instance is not a VideoFile or RawPdfFile.
        """
        text = ""
        anonym_text = ""

        if isinstance(instance, VideoFile):
            media_type = "video"
            created_at = instance.uploaded_at
            filename = instance.original_file_name or (
                instance.raw_file.name.split("/")[-1]
                if instance.raw_file
                else "unknown"
            )

            # ------- anonymization status using VideoState model
            vs = instance.get_or_create_state()
            anonym_status = (
                vs.anonymization_status if vs else VideoAnonymizationState.NOT_STARTED
            )

            # ------- annotation status (validated label segments)
            if instance.label_video_segments.filter(state__is_validated=True).exists():
                annot_status = "validated"
            else:
                annot_status = "not_started"

        elif isinstance(instance, RawPdfFile):
            media_type = "pdf"
            created_at = instance.date_created
            filename = instance.file.name.split("/")[-1] if instance.file else "unknown"

            # ------- anonymization status using RawPdfState model
            rps = instance.get_or_create_state()
            anonym_status = (
                rps.anonymization_status if rps else PdfAnonymizationState.NOT_STARTED
            )

            # ------- annotation status (not applicable for PDFs)
            annot_status = (
                            PdfAnonymizationState.VALIDATED if rps.anonymization_validated else PdfAnonymizationState.NOT_STARTED 
                            )

        else:  # shouldn't happen
            raise TypeError(f"Unsupported instance for overview: {type(instance)}")

        return {
            "id": instance.pk,
            "filename": filename,
            "mediaType": media_type,
            "anonymizationStatus": anonym_status,
            "annotationStatus": annot_status,
            "createdAt": created_at,
        }
