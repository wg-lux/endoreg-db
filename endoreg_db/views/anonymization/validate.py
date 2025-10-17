import logging
from typing import Any, Dict, cast

from django.db import transaction
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from endoreg_db.models import RawPdfFile, VideoFile
from endoreg_db.serializers.anonymization import SensitiveMetaValidateSerializer


logger = logging.getLogger(__name__)


class AnonymizationValidateView(APIView):
    """
    POST /api/anonymization/<int:file_id>/validate/
    
    Validiert und aktualisiert SensitiveMeta-Felder für Videos oder PDFs.
    
    Body (Datumsfelder bevorzugt in deutschem Format DD.MM.YYYY; ISO YYYY-MM-DD ebenfalls akzeptiert):
    {
      "patient_first_name": "Max",
      "patient_last_name":  "Mustermann",
      "patient_dob":        "21.03.1994",      // DD.MM.YYYY bevorzugt
      "examination_date":   "15.02.2024",      // DD.MM.YYYY bevorzugt
      "casenumber":         "12345",
      "anonymized_text":    "...",             // nur für PDFs; Videos ignorieren
      "is_verified":        true               // optional; default true
      "file_type":        "video"            // optional; "video" oder "pdf"; wenn nicht angegeben, wird zuerst Video, dann PDF versucht
    }
    
    Rückwärtskompatibilität: ISO-Format (YYYY-MM-DD) wird ebenfalls akzeptiert.
    """

    @transaction.atomic
    def post(self, request, file_id: int):
        # Serializer-Validierung mit deutscher Datums-Priorität
        serializer = SensitiveMetaValidateSerializer(data=request.data or {})
        serializer.is_valid(raise_exception=True)
        validated_data = cast(Dict[str, Any], serializer.validated_data)
        payload: Dict[str, Any] = dict(validated_data)
        if "is_verified" not in payload:
            payload["is_verified"] = True

        file_type = payload.get("file_type")

        # Try Video first (unless explicitly requesting PDF)
        if file_type in (None, "video"):
            video = VideoFile.objects.select_related("center").filter(pk=file_id).first()
            if video is not None:
                prepared_payload = self._prepare_payload(payload, video)
                try:
                    ok = video.validate_metadata_annotation(prepared_payload)
                except Exception:  # pragma: no cover - defensive safety net
                    logger.exception("Video validation crashed for id=%s", file_id)
                    return Response(
                        {"error": "Video validation encountered an unexpected error."},
                        status=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    )

                if not ok:
                    return Response({"error": "Video validation failed."}, status=status.HTTP_400_BAD_REQUEST)

                return Response({"message": "Video validated."}, status=status.HTTP_200_OK)

            if file_type == "video":
                return Response({"error": f"Video {file_id} not found."}, status=status.HTTP_404_NOT_FOUND)

        # Then PDF (unless explicitly requesting Video)
        if file_type in (None, "pdf"):
            pdf = RawPdfFile.objects.select_related("center").filter(pk=file_id).first()
            if pdf is not None:
                prepared_payload = self._prepare_payload(payload, pdf)
                try:
                    ok = pdf.validate_metadata_annotation(prepared_payload)
                except Exception:  # pragma: no cover - defensive safety net
                    logger.exception("PDF validation crashed for id=%s", file_id)
                    return Response(
                        {"error": "PDF validation encountered an unexpected error."},
                        status=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    )

                if not ok:
                    return Response({"error": "PDF validation failed."}, status=status.HTTP_400_BAD_REQUEST)

                return Response({"message": "PDF validated."}, status=status.HTTP_200_OK)

            if file_type == "pdf":
                return Response({"error": f"PDF {file_id} not found."}, status=status.HTTP_404_NOT_FOUND)

        return Response({"error": f"Item {file_id} not found as video or pdf."}, status=status.HTTP_404_NOT_FOUND)

    @staticmethod
    def _prepare_payload(base_payload: Dict[str, Any], file_obj: Any) -> Dict[str, Any]:
        """Return a fresh payload tailored for the given file object."""

        prepared = dict(base_payload)
        prepared.pop("file_type", None)

        center = getattr(file_obj, "center", None)
        center_name = getattr(center, "name", None)
        if center_name and not prepared.get("center_name"):
            prepared["center_name"] = center_name

        return prepared