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
from pydantic import BaseModel, ValidationError as PydanticValidationError

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
from endoreg_db.views.access_control import (
    assert_center_id_allowed,
    filter_center_scoped_queryset,
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


class _MedicalLedgerPayload(Protocol):
    def model_dump(self, *, mode: str) -> dict[str, object]: ...


class MedicalLedgerContractUnavailable(RuntimeError):
    """Raised when EndoReg is running without the required lx-dtypes contract."""


def _medical_contract_module_missing(exc: ModuleNotFoundError) -> bool:
    return bool(
        exc.name
        and (
            exc.name == "lx_dtypes.models.ledger.medical"
            or exc.name.startswith("lx_dtypes.models.ledger.medical.")
        )
    )


def _build_patient_medical_ledger(patient: Patient) -> _MedicalLedgerPayload:
    try:
        from endoreg_db.services.medical_ledger import (
            build_patient_medical_ledger_for_patient,
        )
    except ModuleNotFoundError as exc:
        if not _medical_contract_module_missing(exc):
            raise
        raise MedicalLedgerContractUnavailable from exc
    return build_patient_medical_ledger_for_patient(patient)


def _medical_contract_unavailable_response() -> Response:
    return Response(
        {
            "code": "medical-ledger-contract-unavailable",
            "detail": (
                "The installed lx-dtypes package does not provide the "
                "medical ledger contract."
            ),
        },
        status=status.HTTP_503_SERVICE_UNAVAILABLE,
    )


def _medical_validation_response(exc: PydanticValidationError) -> Response:
    errors = [
        {
            "field": ".".join(str(part) for part in error["loc"]),
            "message": error["msg"],
            "type": error["type"],
        }
        for error in exc.errors(include_url=False)
    ]
    return Response(
        {"code": "validation-error", "errors": errors},
        status=status.HTTP_422_UNPROCESSABLE_ENTITY,
    )


def _medical_reference_conflict_response(field_name: str) -> Response:
    return Response(
        {
            "code": "reference-conflict",
            "field": field_name,
            "detail": "A medical terminology reference could not be resolved.",
        },
        status=status.HTTP_409_CONFLICT,
    )


def _medical_idempotency_key_required_response() -> Response:
    return Response(
        {
            "code": "idempotency-key-required",
            "detail": (
                "A non-empty Idempotency-Key header of at most 255 characters "
                "is required."
            ),
        },
        status=status.HTTP_422_UNPROCESSABLE_ENTITY,
    )


def _medical_idempotency_conflict_response() -> Response:
    return Response(
        {
            "code": "idempotency-conflict",
            "detail": "The Idempotency-Key was already used for another payload.",
        },
        status=status.HTTP_409_CONFLICT,
    )


def _medical_resource_not_found_response() -> Response:
    return Response(
        {
            "code": "not-found",
            "detail": "Patient medical record not found.",
        },
        status=status.HTTP_404_NOT_FOUND,
    )


def _validate_medical_payload(
    model: type[BaseModel], request: Request
) -> BaseModel | Response:
    try:
        return model.model_validate(request.data)
    except PydanticValidationError as exc:
        return _medical_validation_response(exc)


staff_member_access_control: Callable[..., Any] = cast(
    Callable[..., Any], staff_member_required
)


@staff_member_access_control  # Ensures only staff members can access the page
def start_examination(request: HttpRequest) -> HttpResponse:
    return render(request, "admin/start_examination.html")  # Loads the simple HTML page


class PatientViewSet(viewsets.ModelViewSet[Patient]):  # pyright: ignore[reportInvalidTypeArguments]
    """API endpoint for managing patients."""

    queryset = Patient.objects.all()
    serializer_class = PatientSerializer
    permission_classes = [PolicyPermission]

    def get_queryset(self):
        return filter_center_scoped_queryset(
            queryset=Patient.objects.all(),
            user=self.request.user,
        )

    @action(detail=True, methods=["get", "post"], url_path="medical-ledger")
    def medical_ledger(self, request: Request, pk: int | None = None) -> Response:
        """Read or atomically create this visible patient's medical ledger."""
        del pk
        patient = self.get_object()
        if request.method == "POST":
            try:
                from lx_dtypes.models.ledger.medical.Write import (
                    PatientMedicalLedgerCreate,
                )
                from endoreg_db.services.medical_ledger import (
                    MedicalLedgerIdempotencyConflict,
                    MedicalLedgerIdempotencyKeyInvalid,
                    MedicalLedgerReferenceConflict,
                    create_patient_medical_ledger,
                )
            except ModuleNotFoundError as exc:
                if not _medical_contract_module_missing(exc):
                    raise
                return _medical_contract_unavailable_response()

            validated = _validate_medical_payload(
                PatientMedicalLedgerCreate,
                request,
            )
            if isinstance(validated, Response):
                return validated
            try:
                result = create_patient_medical_ledger(
                    patient=patient,
                    payload=cast(PatientMedicalLedgerCreate, validated),
                    idempotency_key=request.headers.get("Idempotency-Key", ""),
                )
            except MedicalLedgerIdempotencyKeyInvalid:
                return _medical_idempotency_key_required_response()
            except MedicalLedgerIdempotencyConflict:
                return _medical_idempotency_conflict_response()
            except MedicalLedgerReferenceConflict as exc:
                return _medical_reference_conflict_response(exc.field_name)
            return Response(
                result.ledger.model_dump(mode="json"),
                status=(
                    status.HTTP_200_OK if result.replayed else status.HTTP_201_CREATED
                ),
                headers={"Idempotency-Replayed": str(result.replayed).lower()},
            )
        try:
            ledger = _build_patient_medical_ledger(patient)
        except MedicalLedgerContractUnavailable:
            return _medical_contract_unavailable_response()
        return Response(
            ledger.model_dump(mode="json"),
            status=status.HTTP_200_OK,
        )

    @action(detail=True, methods=["post"], url_path="medications")
    def create_medication(self, request: Request, pk: int | None = None) -> Response:
        """Create one validated medication record for this visible patient."""
        del pk
        try:
            from lx_dtypes.models.ledger.medical.Write import PatientMedicationCreate
            from endoreg_db.services.medical_ledger import (
                MedicalLedgerReferenceConflict,
                create_patient_medication,
            )
        except ModuleNotFoundError as exc:
            if not _medical_contract_module_missing(exc):
                raise
            return _medical_contract_unavailable_response()

        validated = _validate_medical_payload(PatientMedicationCreate, request)
        if isinstance(validated, Response):
            return validated
        try:
            medication = create_patient_medication(
                patient=self.get_object(),
                payload=cast(PatientMedicationCreate, validated),
            )
        except MedicalLedgerReferenceConflict as exc:
            return _medical_reference_conflict_response(exc.field_name)
        return Response(
            medication.model_dump(mode="json"),
            status=status.HTTP_201_CREATED,
        )

    @action(
        detail=True,
        methods=["patch"],
        url_path=r"medications/(?P<medication_id>[^/.]+)",
    )
    def update_medication(
        self,
        request: Request,
        pk: int | None = None,
        medication_id: str | None = None,
    ) -> Response:
        """Patch one medication owned by this visible patient."""
        del pk
        try:
            parsed_id = int(medication_id or "")
        except ValueError:
            return _medical_resource_not_found_response()
        try:
            from lx_dtypes.models.ledger.medical.Write import PatientMedicationUpdate
            from endoreg_db.services.medical_ledger import (
                MedicalLedgerPatientResourceNotFound,
                MedicalLedgerReferenceConflict,
                update_patient_medication,
            )
        except ModuleNotFoundError as exc:
            if not _medical_contract_module_missing(exc):
                raise
            return _medical_contract_unavailable_response()

        validated = _validate_medical_payload(PatientMedicationUpdate, request)
        if isinstance(validated, Response):
            return validated
        try:
            medication = update_patient_medication(
                patient=self.get_object(),
                medication_id=parsed_id,
                payload=cast(PatientMedicationUpdate, validated),
            )
        except MedicalLedgerPatientResourceNotFound:
            return _medical_resource_not_found_response()
        except MedicalLedgerReferenceConflict as exc:
            return _medical_reference_conflict_response(exc.field_name)
        return Response(
            medication.model_dump(mode="json"),
            status=status.HTTP_200_OK,
        )

    @action(detail=True, methods=["post"], url_path="medication-schedules")
    def create_medication_schedule(
        self, request: Request, pk: int | None = None
    ) -> Response:
        """Create one schedule from medication records owned by this patient."""
        del pk
        try:
            from lx_dtypes.models.ledger.medical.Write import (
                PatientMedicationScheduleCreate,
            )
            from endoreg_db.services.medical_ledger import (
                MedicalLedgerPatientResourceNotFound,
                create_patient_medication_schedule,
            )
        except ModuleNotFoundError as exc:
            if not _medical_contract_module_missing(exc):
                raise
            return _medical_contract_unavailable_response()

        validated = _validate_medical_payload(PatientMedicationScheduleCreate, request)
        if isinstance(validated, Response):
            return validated
        try:
            schedule = create_patient_medication_schedule(
                patient=self.get_object(),
                payload=cast(PatientMedicationScheduleCreate, validated),
            )
        except MedicalLedgerPatientResourceNotFound:
            return _medical_resource_not_found_response()
        return Response(
            schedule.model_dump(mode="json"),
            status=status.HTTP_201_CREATED,
        )

    @action(
        detail=True,
        methods=["patch"],
        url_path=r"medication-schedules/(?P<schedule_id>[^/.]+)",
    )
    def update_medication_schedule(
        self,
        request: Request,
        pk: int | None = None,
        schedule_id: str | None = None,
    ) -> Response:
        """Replace the medication membership of one patient-owned schedule."""
        del pk
        try:
            parsed_id = int(schedule_id or "")
        except ValueError:
            return _medical_resource_not_found_response()
        try:
            from lx_dtypes.models.ledger.medical.Write import (
                PatientMedicationScheduleUpdate,
            )
            from endoreg_db.services.medical_ledger import (
                MedicalLedgerPatientResourceNotFound,
                update_patient_medication_schedule,
            )
        except ModuleNotFoundError as exc:
            if not _medical_contract_module_missing(exc):
                raise
            return _medical_contract_unavailable_response()

        validated = _validate_medical_payload(PatientMedicationScheduleUpdate, request)
        if isinstance(validated, Response):
            return validated
        try:
            schedule = update_patient_medication_schedule(
                patient=self.get_object(),
                schedule_id=parsed_id,
                payload=cast(PatientMedicationScheduleUpdate, validated),
            )
        except MedicalLedgerPatientResourceNotFound:
            return _medical_resource_not_found_response()
        return Response(
            schedule.model_dump(mode="json"),
            status=status.HTTP_200_OK,
        )

    def perform_create(self, serializer: serializers.BaseSerializer[Patient]) -> None:  # pyright: ignore[reportIncompatibleMethodOverride]
        """Erweiterte Validierung beim Erstellen eines Patienten"""
        center = serializer.validated_data.get("center")
        assert_center_id_allowed(
            request=self.request,
            center_id=getattr(center, "pk", None),
        )
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

        examinations = PatientExamination.objects.filter(patient=patient)
        examination_count = examinations.count()
        finding_count = PatientFinding.objects.filter(
            patient_examination__patient=patient
        ).count()
        video_count = examinations.filter(video_files__isnull=False).count()
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
