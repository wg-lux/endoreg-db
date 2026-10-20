# pyright: reportIncompatibleMethodOverride=false, reportUnusedClass=false
from __future__ import annotations

from datetime import date
from typing import Protocol, TypedDict, cast

from rest_framework import serializers
from rest_framework.request import Request

from endoreg_db.models.administration.person.patient.patient import Patient
from endoreg_db.models.medical.examination.examination import Examination
from endoreg_db.models.medical.finding.finding import Finding
from endoreg_db.models.medical.patient.patient_examination import PatientExamination
from endoreg_db.services.centers.access import resolve_allowed_center_ids
from lx_dtypes.models.contracts.knowledge_base import KnowledgeBaseIdentity
from lx_dtypes.models.contracts.patient_examination import (
    PatientExaminationPatientDataPayload,
)
from lx_dtypes.terminology.terminology_loader import load_module_kb
from lx_dtypes.terminology.terminology_service import TerminologyError


class _PatientExaminationOutputLike(Protocol):
    patient: Patient
    examination: Examination | None


class _PatientExaminationSaveSource(Protocol):
    def save(self) -> None: ...


class _PatientExaminationWriteData(TypedDict, total=False):
    patient: Patient
    examination: Examination | None
    date_start: date | None
    date_end: date | None
    hash: str
    knowledge_base_module: str
    knowledge_base_version: str


class PatientExaminationSerializer(serializers.ModelSerializer[PatientExamination]):
    patient = serializers.IntegerField(
        write_only=True,
        required=True,
        min_value=1,
        help_text="Local patient ID",
    )
    patient_data = serializers.SerializerMethodField(read_only=True)
    examination: object = cast(
        object,
        serializers.SlugRelatedField(
            slug_field="name",
            queryset=Examination.objects.all(),
            required=False,
            allow_null=True,
        ),
    )
    patient_name = serializers.SerializerMethodField()
    examination_name = serializers.SerializerMethodField()

    class Meta:  # pyright: ignore[reportIncompatibleVariableOverride]
        model = PatientExamination
        fields = [
            "id",
            "patient",
            "patient_data",
            "examination",
            "date_start",
            "date_end",
            "knowledge_base_module",
            "knowledge_base_version",
            "patient_name",
            "examination_name",
        ]
        read_only_fields = [
            "id",
            "patient_name",
            "examination_name",
            "patient_data",
        ]

    def get_patient_data(
        self, obj: _PatientExaminationOutputLike
    ) -> dict[str, str | int]:
        patient = obj.patient
        if patient.pk is None:
            raise serializers.ValidationError("Patient ist nicht gespeichert")
        payload = PatientExaminationPatientDataPayload(
            id=patient.pk,
            first_name=patient.first_name,
            last_name=patient.last_name,
        )
        return cast(dict[str, str | int], payload.model_dump(mode="python"))

    def get_patient_name(self, obj: _PatientExaminationOutputLike) -> str:
        patient = obj.patient
        return f"{patient.first_name} {patient.last_name}"

    def get_examination_name(self, obj: _PatientExaminationOutputLike) -> str:
        examination = obj.examination
        if examination is None:
            return ""
        return examination.name

    def validate_patient(self, value: int) -> Patient:
        request = self.context.get("request")
        if not isinstance(request, Request):
            raise serializers.ValidationError("Autorisierter Anfragekontext fehlt")
        center_ids = resolve_allowed_center_ids(request.user)
        patients = Patient.objects.filter(pk=value)
        if center_ids is not None:
            patients = patients.filter(center_id__in=center_ids)
        patient = patients.first()
        if patient is None:
            raise serializers.ValidationError(
                "Patient ist im autorisierten Zentrum nicht eindeutig verfügbar"
            )
        return patient

    def validate_date_start(self, value: date | None) -> date | None:
        if value is not None and value > date.today():
            raise serializers.ValidationError(
                "Startdatum kann nicht in der Zukunft liegen"
            )
        return value

    def validate_date_end(self, value: date | None) -> date | None:
        if value is not None and value > date.today():
            raise serializers.ValidationError(
                "Enddatum kann nicht in der Zukunft liegen"
            )
        return value

    def validate(
        self, attrs: _PatientExaminationWriteData
    ) -> _PatientExaminationWriteData:
        date_start = attrs.get("date_start")
        date_end = attrs.get("date_end")
        if date_start is not None and date_end is not None and date_end < date_start:
            raise serializers.ValidationError(
                "Enddatum muss nach dem Startdatum liegen"
            )
        has_module = "knowledge_base_module" in attrs
        has_version = "knowledge_base_version" in attrs
        if has_module != has_version:
            raise serializers.ValidationError(
                {
                    "knowledge_base_module": (
                        "knowledge_base_module and knowledge_base_version must be "
                        "submitted together"
                    )
                }
            )
        if has_module and has_version:
            try:
                identity = KnowledgeBaseIdentity(
                    knowledge_base_module=attrs["knowledge_base_module"],
                    knowledge_base_version=attrs["knowledge_base_version"],
                )
                load_module_kb(
                    identity.knowledge_base_module,
                    version=identity.knowledge_base_version,
                )
            except (TerminologyError, LookupError, ValueError) as exc:
                raise serializers.ValidationError(
                    {"knowledge_base_module": str(exc)}
                ) from exc
        return attrs

    def create(
        self, validated_data: _PatientExaminationWriteData
    ) -> PatientExamination:
        try:
            patient = validated_data.pop("patient")
            validated_data["patient"] = patient
            return PatientExamination.objects.create(**validated_data)
        except Exception as exc:
            raise serializers.ValidationError(
                f"Fehler beim Erstellen der Patientenuntersuchung: {exc}"
            ) from exc

    def update(
        self,
        instance: PatientExamination,
        validated_data: _PatientExaminationWriteData,
    ) -> PatientExamination:
        try:
            if "patient" in validated_data:
                patient = validated_data.pop("patient")
                validated_data["patient"] = patient

            for attr, value in validated_data.items():
                setattr(instance, attr, value)
            cast(_PatientExaminationSaveSource, instance).save()
            return instance
        except Exception as exc:
            raise serializers.ValidationError(
                f"Fehler beim Aktualisieren der Patientenuntersuchung: {exc}"
            ) from exc

    def get_findings(self, patient_examination_id: int) -> list[Finding]:
        instance = PatientExamination.objects.filter(pk=patient_examination_id).first()
        if instance is None:
            return []
        return instance.get_available_findings()
