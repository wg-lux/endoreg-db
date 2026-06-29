from __future__ import annotations

from datetime import date
from typing import Protocol, cast

from rest_framework import serializers
from endoreg_db.models.administration.person.patient.patient import Patient
from endoreg_db.models.other.gender import Gender
from endoreg_db.serializers.fields import CenterKeyRelatedField


class _GenderManager(Protocol):
    def resolve_by_name(self, name: str) -> Gender | None: ...


class _PatientLike(Protocol):
    def save(self) -> None: ...


class GenderNameRelatedField(serializers.SlugRelatedField):
    def to_internal_value(self, data: object) -> Gender:
        gender = cast(_GenderManager, Gender.objects).resolve_by_name(str(data))
        if gender is None:
            raise serializers.ValidationError(f'Gender "{data}" does not exist.')
        return gender


class PatientSerializer(serializers.ModelSerializer[Patient]):
    # Use the slug field "name" so that the gender is represented by its string value
    gender = cast(
        GenderNameRelatedField,
        GenderNameRelatedField(
            slug_field="name",
            queryset=Gender.objects.all(),
            required=False,
            allow_null=True,
        ),
    )
    center = serializers.CharField(source="center.display_name", read_only=True)
    center_key = cast(
        CenterKeyRelatedField,
        CenterKeyRelatedField(
            source="center",
            required=False,
            allow_null=True,
        ),
    )
    age = serializers.SerializerMethodField()

    class Meta:  # pyright: ignore[reportIncompatibleVariableOverride]
        model = Patient
        fields = [
            "id",
            "first_name",
            "last_name",
            "dob",
            "gender",
            "center",
            "center_key",
            "email",
            "phone",
            "patient_hash",
            "is_real_person",
            "age",
        ]
        read_only_fields = ["id", "age"]

    def validate(self, attrs: dict[str, object]) -> dict[str, object]:
        attrs = super().validate(attrs)
        if "center" in self.initial_data and "center_key" not in self.initial_data:
            raise serializers.ValidationError(
                {
                    "center_key": (
                        "center_key is the canonical center identifier for writes; "
                        "the 'center' field is read-only display data."
                    )
                }
            )
        return attrs

    def get_age(self, obj: Patient) -> int | None:
        """Berechnet das Alter des Patienten"""
        if obj.dob:
            return obj.age()
        return None

    def validate_first_name(self, value: str) -> str:
        """Validiert den Vornamen"""
        if not value or not value.strip():
            raise serializers.ValidationError("Vorname ist erforderlich")
        return value.strip()

    def validate_last_name(self, value: str) -> str:
        """Validiert den Nachnamen"""
        if not value or not value.strip():
            raise serializers.ValidationError("Nachname ist erforderlich")
        return value.strip()

    def validate_dob(self, value: date | None) -> date | None:
        """Validiert das Geburtsdatum"""
        if value and value > date.today():
            raise serializers.ValidationError(
                "Geburtsdatum kann nicht in der Zukunft liegen"
            )
        return value

    def validate_email(self, value: str) -> str:
        """Validiert die E-Mail-Adresse"""
        if value and "@" not in value:
            raise serializers.ValidationError("Ungültige E-Mail-Adresse")
        return value

    def create(self, validated_data: dict[str, object]) -> Patient:
        """Erstellt einen neuen Patienten mit verbesserter Fehlerbehandlung"""
        try:
            patient = Patient.objects.create(**validated_data)
            return patient
        except Exception as e:
            raise serializers.ValidationError(
                f"Fehler beim Erstellen des Patienten: {str(e)}"
            )

    def update(self, instance: Patient, validated_data: dict[str, object]) -> Patient:
        """
        Update an existing Patient instance with validated data.

        Parameters:
            instance (Patient): The Patient instance to update.
            validated_data (dict): Dictionary of validated data to update the instance with.

        Returns:
            Patient: The updated Patient instance.

        Raises:
            ValidationError: If an error occurs during the update process.
        """
        try:
            for attr, value in validated_data.items():
                setattr(instance, attr, value)
            cast(_PatientLike, instance).save()
            return instance
        except Exception as e:
            raise serializers.ValidationError(
                f"Fehler beim Aktualisieren des Patienten: {str(e)}"
            )
