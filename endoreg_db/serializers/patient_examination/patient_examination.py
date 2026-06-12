# pyright: reportIncompatibleMethodOverride=false, reportUnusedClass=false
from __future__ import annotations

from datetime import date
from typing import Protocol, TypedDict, cast

from rest_framework import serializers

from endoreg_db.models.administration.person.patient.patient import Patient
from endoreg_db.models.medical.examination.examination import Examination
from endoreg_db.models.medical.finding.finding import Finding
from endoreg_db.models.medical.patient.patient_examination import PatientExamination
from lx_dtypes.models.contracts.patient_examination import (
    PatientExaminationPatientDataPayload,
)


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
    patient = serializers.CharField(
        write_only=True,
        required=True,
        help_text="Patient Hash (z.B. 'patient_2')",
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

    class Meta:  # type: ignore[reportIncompatibleVariableOverride]
        model = PatientExamination
        fields = [
            "id",
            "patient",
            "patient_data",
            "examination",
            "date_start",
            "date_end",
            "hash",
            "knowledge_base_module",
            "knowledge_base_version",
            "patient_name",
            "examination_name",
        ]
        read_only_fields = [
            "id",
            "hash",
            "knowledge_base_module",
            "knowledge_base_version",
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
        patient_hash = patient.patient_hash
        if not patient_hash:
            raise serializers.ValidationError("Patient Hash fehlt")
        payload = PatientExaminationPatientDataPayload(
            id=patient.pk,
            patient_hash=patient_hash,
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

    def validate_patient(self, value: str) -> Patient:
        if not value:
            raise serializers.ValidationError("Patient Hash ist erforderlich")

        try:
            return Patient.objects.get(patient_hash=value)
        except Patient.DoesNotExist:
            return Patient.objects.create(
                patient_hash=value,
                first_name="Patient",
                last_name=value,
                is_real_person=False,
            )

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
