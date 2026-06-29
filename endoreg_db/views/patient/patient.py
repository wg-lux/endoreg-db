from __future__ import annotations

from typing import Any, Callable, Protocol, cast

from django.contrib.admin.views.decorators import staff_member_required  # pyright: ignore[reportUnknownVariableType]
from django.http import HttpRequest, HttpResponse
from django.shortcuts import render
from django.db import transaction

from rest_framework import serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.request import Request
from rest_framework.response import Response

from endoreg_db.authz.permissions import PolicyPermission
from endoreg_db.models.administration.person.patient.patient import Patient
from endoreg_db.models.medical.patient.patient_examination import PatientExamination
from endoreg_db.models.medical.patient.patient_finding import PatientFinding
from endoreg_db.models.media.pdf.raw_pdf import RawPdfFile
from endoreg_db.serializers.patient import PatientSerializer
from lx_dtypes.models.contracts.patient_view import (
    PatientDeletionRelatedObjectsPayload,
    PatientDeletionSafetyPayload,
    PatientPseudonymPayload,
)


class _PatientNameLike(Protocol):
    first_name: str
    last_name: str
    is_real_person: bool | None
    id: int | str | None


class _PatientDeleteLike(_PatientNameLike, Protocol):
    def delete(self) -> tuple[int, dict[str, int]]: ...


class _PatientIdLike(Protocol):
    id: int | str | None


staff_member_access_control: Callable[..., Any] = cast(
    Callable[..., Any], staff_member_required
)


@staff_member_access_control  # Ensures only staff members can access the page
def start_examination(request: HttpRequest) -> HttpResponse:
    return render(request, "admin/start_examination.html")  # Loads the simple HTML page


class PatientViewSet(viewsets.ModelViewSet):
    """API endpoint for managing patients."""

    queryset = Patient.objects.all()
    serializer_class = PatientSerializer
    permission_classes = [PolicyPermission]

    def perform_create(self, serializer: serializers.BaseSerializer[Patient]) -> None:  # pyright: ignore[reportIncompatibleMethodOverride]
        """Erweiterte Validierung beim Erstellen eines Patienten"""
        try:
            serializer.save()
        except Exception as e:
            raise serializers.ValidationError(
                f"Fehler beim Erstellen des Patienten: {str(e)}"
            )

    def update(
        self,
        request: Request,
        *args: Any,
        **kwargs: Any,
    ) -> Response:
        """Erweiterte Logik für das Aktualisieren von Patienten"""
        try:
            return super().update(request, *args, **kwargs)
        except Exception as e:
            return Response(
                {"error": f"Fehler beim Aktualisieren des Patienten: {str(e)}"},
                status=status.HTTP_400_BAD_REQUEST,
            )

    def destroy(
        self,
        request: Request,
        *args: Any,
        **kwargs: Any,
    ) -> Response:
        """
        Delete a patient with proper error handling and cascade protection.
        """
        patient = self.get_object()
        patient_record = cast(_PatientDeleteLike, patient)

        try:
            with transaction.atomic():
                examination_count = PatientExamination.objects.filter(
                    patient=patient
                ).count()
                finding_count = PatientFinding.objects.filter(
                    patient_examination__patient=patient
                ).count()

                if examination_count > 0:
                    return Response(
                        {
                            "error": "Patient cannot be deleted",
                            "reason": f"Patient has {examination_count} examination(s) and {finding_count} finding(s).",
                            "detail": "Please remove all related examinations and findings before deleting the patient.",
                        },
                        status=status.HTTP_409_CONFLICT,
                    )

                if patient_record.is_real_person:
                    return Response(
                        {
                            "error": "Cannot delete real patient",
                            "reason": "This patient is marked as a real person.",
                            "detail": "Real patient data cannot be deleted for data protection reasons.",
                        },
                        status=status.HTTP_403_FORBIDDEN,
                    )

                patient_name = f"{patient_record.first_name} {patient_record.last_name}"
                patient_record.delete()

                return Response(
                    {
                        "message": f'Patient "{patient_name}" has been successfully deleted.'
                    },
                    status=status.HTTP_200_OK,
                )

        except Exception as e:
            return Response(
                {
                    "error": "Patient deletion failed",
                    "reason": "Patient has protected related objects.",
                    "detail": str(e),
                },
                status=status.HTTP_409_CONFLICT,
            )

    def check_pe_exist(self, request: Request, pk: int | None = None) -> Response:
        """Check if a patient examination exists."""
        if pk is None:
            return Response(
                {"error": "pk must be provided"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            PatientExamination.objects.get(pk=pk)
            return Response({"exists": True}, status=status.HTTP_200_OK)
        except PatientExamination.DoesNotExist:
            return Response({"exists": False}, status=status.HTTP_404_NOT_FOUND)

    @action(detail=True, methods=["get"])
    def check_deletion_safety(
        self, request: Request, pk: int | None = None
    ) -> Response:
        """
        Check if a patient can be safely deleted.
        Returns information about related objects.
        """
        if pk is None:
            return Response(
                {"error": "pk must be provided"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        patient = self.get_object()
        patient_record = cast(_PatientNameLike, patient)
        patient_record = cast(_PatientNameLike, patient)

        examinations = PatientExamination.objects.filter(patient=patient)
        examination_count = examinations.count()
        finding_count = PatientFinding.objects.filter(
            patient_examination__patient=patient
        ).count()
        video_count = examinations.filter(video__isnull=False).count()
        report_count = RawPdfFile.objects.filter(examination__patient=patient).count()
        is_real_person = bool(patient_record.is_real_person)
        can_delete = examination_count == 0 and not is_real_person

        warnings: list[str] = []
        if is_real_person:
            warnings.append("This patient is marked as a real person")
        if examination_count > 0:
            warnings.append(f"Patient has {examination_count} examination(s)")
        if finding_count > 0:
            warnings.append(f"Patient has {finding_count} finding(s)")

        payload = PatientDeletionSafetyPayload(
            can_delete=can_delete,
            is_real_person=is_real_person,
            related_objects=PatientDeletionRelatedObjectsPayload(
                examinations=examination_count,
                findings=finding_count,
                videos=video_count,
                reports=report_count,
            ),
            warnings=warnings,
        )
        return Response(payload.model_dump(mode="python"))

    @action(detail=False, methods=["get"])
    def patient_count(self, request: Request) -> Response:
        """Gibt die Anzahl der Patienten zurück"""
        count = Patient.objects.count()
        return Response({"count": count})

    @action(detail=True, methods=["post"], url_path="pseudonym")
    def generate_pseudonym(self, request: Request, pk: int | None = None) -> Response:
        """
        Generate a pseudonym hash for an existing patient.

        This endpoint generates a deterministic hash based on the patient's
        personal data (name, dob, center) using server-side logic without
        exposing any secrets to the frontend.
        """
        if pk is None:
            return Response(
                {"error": "pk must be provided"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        from endoreg_db.services.pseudonym_service import (
            generate_patient_pseudonym,
            validate_patient_for_pseudonym,
        )

        patient = self.get_object()
        patient_id = cast(_PatientIdLike, patient).id

        try:
            # Validate that patient has required fields
            missing_fields = validate_patient_for_pseudonym(patient)
            if missing_fields:
                return Response(
                    {
                        "error": "Missing required fields for pseudonym generation",
                        "missing_fields": missing_fields,
                        "detail": f"Please provide: {', '.join(missing_fields)}",
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

            # Generate the pseudonym
            patient_hash, persisted = generate_patient_pseudonym(patient)

            if patient_id is None:
                raise ValueError("Patient id is required for pseudonym response")

            payload = PatientPseudonymPayload(
                patient_id=patient_id,
                patient_hash=patient_hash,
                source="server",
                persisted=persisted,
                message="Pseudonym generated successfully",
            )
            return Response(
                payload.model_dump(mode="python"), status=status.HTTP_200_OK
            )

        except ValueError as e:
            return Response(
                {"error": "Pseudonym generation failed", "detail": str(e)},
                status=status.HTTP_400_BAD_REQUEST,
            )
        except Exception as e:
            return Response(
                {
                    "error": "Internal server error during pseudonym generation",
                    "detail": str(e),
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
