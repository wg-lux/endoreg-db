import logging
from typing import Any, Dict, cast

from django.db import transaction
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from endoreg_db.models import RawPdfFile, VideoFile
from endoreg_db.models.metadata import SensitiveMeta
from endoreg_db.serializers.anonymization import SensitiveMetaValidateSerializer
from endoreg_db.utils.operation_log import (
    record_operation,  # only touched the parts where validation succeeds
)

logger = logging.getLogger(__name__)


class AnonymizationValidateView(APIView):
    """
    POST /api/anonymization/<int:file_id>/validate/

    Validiert und aktualisiert SensitiveMeta-Felder für Videos oder reports.

    DATA HERE IS COMING FROM THE ANONYIZATION VALIDATION COMPONENT

    Body (Datumsfelder bevorzugt in deutschem Format DD.MM.YYYY; ISO YYYY-MM-DD ebenfalls akzeptiert):
    {
      "patient_first_name": "Max",
      "patient_last_name":  "Mustermann",
      "patient_dob":        "21.03.1994",      // DD.MM.YYYY bevorzugt
      "patient_gender":     "male"
      "examination_date":   "15.02.2024",      // DD.MM.YYYY bevorzugt

      "casenumber":         "12345",
      "anonymized_text":    "...",             // nur für reports; Videos ignorieren
      "is_verified":        true               // optional; default true
      "file_type":        "video"            // optional; "video" oder "pdf"; wenn nicht angegeben, wird zuerst Video, dann report versucht
      "center_name":       editedPatient.value.centerName || '',
      "external_id":       editedPatient.value.externalId || '',
      "external_id_origin":editedPatient.value.externalIdOrigin || '',
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

        # Default ist_verified = True
        if "is_verified" not in payload:
            payload["is_verified"] = True

        file_type = payload.get("file_type")
        status_before = None

        with transaction.atomic():
            # Try Video first (unless explicitly requesting report)
            if file_type in (None, "video"):
                video = (
                    VideoFile.objects.select_related(
                        "center", "sensitive_meta", "state"
                    )
                    .filter(pk=file_id)
                    .first()
                )
                # TODO: The state for video will be none when no state is set and the state for pdf will always be none. After status needs to be inferred after calling the sensitive meta state update functions
                if video is not None:
                    prepared_payload = self._prepare_payload(payload, video)
                    try:
                        ok = video.validate_metadata_annotation(prepared_payload)
                    except Exception:  # pragma: no cover - defensive safety net
                        logger.exception("Video validation crashed for id=%s", file_id)
                        return Response(
                            {
                                "error": "Video validation encountered an unexpected error."
                            },
                            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
                        )

                    if not ok:
                        return Response(
                            {"error": "Video validation failed."},
                            status=status.HTTP_400_BAD_REQUEST,
                        )

                    # this is here for tests!
                    if video.sensitive_meta is None:
                        sm = SensitiveMeta.objects.create(center=video.center)
                        video.sensitive_meta = sm

                    video.save(update_fields=["sensitive_meta"])
                    video.sensitive_meta.get_or_create_state()
                    if video.sensitive_meta.state is not None:
                        video.sensitive_meta.state.refresh_from_db()
                        video.sensitive_meta.state.mark_dob_verified()
                        video.sensitive_meta.state.mark_names_verified()
                        video.sensitive_meta.create_anonymized_record()
                    else:
                        return Response(
                            {"message": "Video not validated, failed to create State."},
                            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
                        )

                    if video.state is not None:
                        video.state.anonymized = True
                        video.sensitive_meta.state.save()
                    try:
                        if video.state is not None:
                            st = getattr(video.state, "anonymization_status", None)
                            if st is not None:
                                status_before = str(getattr(st, "value", st))
                    except Exception:
                        logger.exception(
                            "Failed to read video anonymization_status before validation"
                        )

                        # --- NEW: status AFTER validation ---
                    status_after = status_before
                    try:
                        if video.state is not None:
                            video.state.refresh_from_db()
                            st_after = getattr(
                                video.state, "anonymization_status", None
                            )
                            if st_after is not None:
                                status_after = str(getattr(st_after, "value", st_after))
                    except Exception:
                        logger.exception(
                            "Failed to read video anonymization_status after validation"
                        )

                    # --- write operation log ---
                    # TODO: update the function call bases on the status , once merged
                    record_operation(
                        request,
                        action="anonymization.validated",
                        resource_type="video",
                        resource_id=file_id,
                        status_before=status_before,
                        status_after=status_after,
                    )

                    return Response(
                        {"message": "Video validated."},
                        status=status.HTTP_200_OK,
                    )

                if file_type == "video":
                    return Response(
                        {"error": f"Video {file_id} not found."},
                        status=status.HTTP_404_NOT_FOUND,
                    )

            # Then report (unless explicitly requesting Video)
            if file_type in (None, "pdf"):
                pdf = (
                    RawPdfFile.objects.select_related(
                        "center", "sensitive_meta", "state"
                    )
                    .filter(pk=file_id)
                    .first()
                )
                if pdf is not None:
                    prepared_payload = self._prepare_payload(payload, pdf)
                    try:
                        ok = pdf.validate_metadata_annotation(prepared_payload)
                    except Exception:  # pragma: no cover - defensive safety net
                        logger.exception("report validation crashed for id=%s", file_id)
                        return Response(
                            {
                                "error": "report validation encountered an unexpected error."
                            },
                            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
                        )

                    # sanity logging – but don't break flow
                    try:
                        assert pdf.sensitive_meta is not None
                        assert pdf.sensitive_meta.state is not None
                    except AssertionError as e:
                        logger.error("%s", e)

                    if not ok:
                        return Response(
                            {"error": "report validation failed."},
                            status=status.HTTP_400_BAD_REQUEST,
                        )
                    else:
                        # this is here for tests!
                        if pdf.sensitive_meta is None:
                            sm = SensitiveMeta.objects.create(center=pdf.center)
                            pdf.sensitive_meta = sm

                        pdf.save(update_fields=["sensitive_meta"])
                        pdf.sensitive_meta.get_or_create_state()
                        if (
                                pdf.sensitive_meta
                                and pdf.sensitive_meta.state
                            ):
                                pdf.sensitive_meta.state.refresh_from_db()
                                pdf.sensitive_meta.state.mark_dob_verified()
                                pdf.sensitive_meta.state.mark_names_verified()
                                pdf.sensitive_meta.create_anonymized_record()
                                
                                if pdf.state:
                                    pdf.state.mark_anonymized()
                                    pdf.state.save(update_fields=["anonymized"])
                                    
                                pdf.sensitive_meta.state.save()
                        else:
                            return Response(
                                {"message": "report not validated, failed to create State."},
                                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
                            )

                    status_after = status_before
                    try:
                        if pdf.state is not None:
                            pdf.state.refresh_from_db()
                            st_after = getattr(pdf.state, "anonymization_status", None)
                            if st_after is not None:
                                status_after = str(getattr(st_after, "value", st_after))
                    except Exception:
                        logger.exception(
                            "Failed to read pdf anonymization_status after validation"
                        )

                    # --- NEW: write operation log ---
                    record_operation(
                        request,
                        action="anonymization.validated",
                        resource_type="pdf",
                        resource_id=file_id,
                        status_before=status_before,
                        status_after=status_after,
                    )

                    return Response(
                        {"message": "report validated."},
                        status=status.HTTP_200_OK,
                    )

                if file_type == "pdf":
                    return Response(
                        {"error": f"report {file_id} not found."},
                        status=status.HTTP_404_NOT_FOUND,
                    )

        return Response(
            {"error": f"Item {file_id} not found as video or pdf."},
            status=status.HTTP_404_NOT_FOUND,
        )

    @staticmethod
    def _prepare_payload(base_payload: Dict[str, Any], file_obj: Any) -> Dict[str, Any]:
        """
        Return a fresh payload tailored for the given file object.

        - Strips `file_type` before forwarding to validators.
        - Injects `center_name` from the file's center if not already present.
        - Normalizes `patient_gender` if present, but does NOT require it.
        """
        prepared: Dict[str, Any] = dict(base_payload)

        # never send file_type to validators
        prepared.pop("file_type", None)

        # center_name from file.center if not already set
        center = getattr(file_obj, "center", None)
        center_name = getattr(center, "name", None)
        if center_name and not prepared.get("center_name"):
            prepared["center_name"] = center_name

        # Gender normalization: optional, robust against missing or unknown values
        raw_gender = base_payload.get("patient_gender", None)
        if raw_gender is None:
            # nothing provided → don't touch gender
            return prepared

        gender = str(raw_gender).strip().lower()

        # empty string behaves as "not set" – don't override anything
        if gender == "":
            return prepared

        male_values = {"m", "male", "männlich"}
        female_values = {"w", "f", "female", "weiblich"}

        if gender in male_values:
            prepared["patient_gender"] = "male"
        elif gender in female_values:
            prepared["patient_gender"] = "female"
        else:
            # keep existing semantics: unknown values default to "male"
            logger.warning(
                "Unsupported patient_gender value %r; defaulting to 'male'", raw_gender
            )
            prepared["patient_gender"] = "male"

        return prepared
