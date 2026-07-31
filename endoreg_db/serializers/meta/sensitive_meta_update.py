# pyright: reportIncompatibleMethodOverride=false, reportUnusedClass=false
from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol, cast

from django.db import transaction
from rest_framework import serializers
import logging

from endoreg_db.models.administration.center.center import Center
from endoreg_db.models.metadata.sensitive_meta import SensitiveMeta
from endoreg_db.models.other.gender import Gender
from lx_dtypes.models.contracts.sensitive_meta_update import SensitiveMetaUpdatePayload

logger = logging.getLogger(__name__)


class _SensitiveMetaUpdateInstance(Protocol):
    pk: int | None
    center: Center
    patient_gender: Gender

    def update_from_dict(self, data: Mapping[str, object]) -> None: ...

    def get_or_create_state(self) -> object: ...


class _CenterManager(Protocol):
    def get_by_natural_key(self, name: str) -> Center: ...


class _GenderManager(Protocol):
    def resolve_by_name(self, name: str) -> Gender | None: ...


class _State(Protocol):
    dob_verified: bool
    names_verified: bool

    def save(self, *args: object, **kwargs: object) -> None: ...


class SensitiveMetaUpdateSerializer(serializers.ModelSerializer[SensitiveMeta]):
    """
    Serializer for updating SensitiveMeta fields including verification state.
    Handles partial updates and state management.
    """

    # Verification state fields
    dob_verified = serializers.BooleanField(required=False)
    names_verified = serializers.BooleanField(required=False)

    # Center can be updated by name
    center_name = serializers.CharField(write_only=True, required=False)

    # Gender can be updated by name
    patient_gender_name = serializers.CharField(write_only=True, required=False)

    class Meta:  # pyright: ignore[reportIncompatibleVariableOverride]
        model = SensitiveMeta
        fields = [
            "patient_first_name",
            "patient_last_name",
            "patient_dob",
            "examination_date",
            "center_name",
            "patient_gender_name",
            "endoscope_type",
            "endoscope_sn",
            "examiner_first_name",
            "examiner_last_name",
            "dob_verified",
            "names_verified",
        ]

    def validate_center_name(self, value: str) -> str:
        """
        Validates that a center with the given natural key exists.

        Raises a validation error if the specified center does not exist.
        """
        if value:
            try:
                if not Center.objects.filter(name=value).exists():
                    raise Center.DoesNotExist
                return value
            except Center.DoesNotExist:
                raise serializers.ValidationError(f"Center '{value}' does not exist.")
        return value

    def validate_patient_gender_name(self, value: str) -> str:
        """
        Validates that a gender with the given name exists.

        Raises a validation error if no matching Gender is found.
        """
        if value:
            gender = Gender.objects.filter(name=value).first()
            if gender is None:
                raise serializers.ValidationError(f"Gender '{value}' does not exist.")
            return value
        return value

    def validate(self, attrs: Mapping[str, object]) -> Mapping[str, object]:
        """
        Validate that patient first and last names, if provided, are not empty strings.

        Raises a validation error if either `patient_first_name` or `patient_last_name` is present but empty.
        """
        # Ensure names are not empty if provided
        first_name = cast(str, attrs.get("patient_first_name", ""))
        if first_name and not first_name.strip():
            raise serializers.ValidationError(
                {"patient_first_name": "First name cannot be empty."}
            )

        last_name = cast(str, attrs.get("patient_last_name", ""))
        if last_name and not last_name.strip():
            raise serializers.ValidationError(
                {"patient_last_name": "Last name cannot be empty."}
            )

        return attrs

    @transaction.atomic
    def update(
        self,
        instance: SensitiveMeta,
        validated_data: Mapping[str, object],
    ) -> SensitiveMeta:
        """
        Updates a SensitiveMeta instance with provided data, including related center, gender, and verification state fields.

        Handles assignment of related Center and Gender objects by name, updates model fields, and manages verification state flags (`dob_verified`, `names_verified`) in the associated state object if provided.

        Returns:
            SensitiveMeta: The updated SensitiveMeta instance.
        """
        # Extract verification state data
        payload = SensitiveMetaUpdatePayload.model_validate(dict(validated_data))
        instance_typed = cast(_SensitiveMetaUpdateInstance, instance)

        # Extract and handle center update
        center_name = payload.center_name
        if center_name:
            try:
                center = Center.objects.filter(name=center_name).first()
                if center is None:
                    raise Center.DoesNotExist
                instance_typed.center = center
            except Center.DoesNotExist:
                logger.error(f"Center '{center_name}' not found during update")
                raise serializers.ValidationError(
                    f"Center '{center_name}' does not exist."
                )

        # Extract and handle gender update
        patient_gender_name = payload.patient_gender_name
        if patient_gender_name:
            gender = Gender.objects.filter(name=patient_gender_name).first()
            if gender is None:
                logger.error(f"Gender '{patient_gender_name}' not found during update")
                raise serializers.ValidationError(
                    f"Gender '{patient_gender_name}' does not exist."
                )
            instance_typed.patient_gender = gender

        # Update regular fields using the model's update_from_dict method
        update_data = dict(validated_data)
        update_data.pop("dob_verified", None)
        update_data.pop("names_verified", None)
        update_data.pop("center_name", None)
        update_data.pop("patient_gender_name", None)
        if update_data:
            instance_typed.update_from_dict(update_data)

        # Update verification state if provided
        if payload.dob_verified or payload.names_verified:
            # Ensure state exists
            state = cast(_State, instance_typed.get_or_create_state())

            if payload.dob_verified:
                state.dob_verified = payload.dob_verified
                logger.info(
                    f"Updated DOB verification for SensitiveMeta {instance.pk}: {payload.dob_verified}"
                )

            if payload.names_verified:
                state.names_verified = payload.names_verified
                logger.info(
                    f"Updated names verification for SensitiveMeta {instance.pk}: {payload.names_verified}"
                )

            state.save()

        return instance
