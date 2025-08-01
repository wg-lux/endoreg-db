from rest_framework import serializers
from django.db import transaction
import logging

from ...models import SensitiveMeta, Center, Gender

logger = logging.getLogger(__name__)



class SensitiveMetaUpdateSerializer(serializers.ModelSerializer):
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

    class Meta:
        model = SensitiveMeta
        fields = [
            'patient_first_name',
            'patient_last_name',
            'patient_dob',
            'examination_date',
            'center_name',
            'patient_gender_name',
            'endoscope_type',
            'endoscope_sn',
            'examiner_first_name',
            'examiner_last_name',
            'dob_verified',
            'names_verified',
        ]

    def validate_center_name(self, value):
        """
        Validates that a center with the given natural key exists.
        
        Raises a validation error if the specified center does not exist.
        """
        if value:
            try:
                Center.objects.get_by_natural_key(value)
                return value
            except Center.DoesNotExist:
                raise serializers.ValidationError(f"Center '{value}' does not exist.")
        return value

    def validate_patient_gender_name(self, value):
        """
        Validates that a gender with the given name exists.
        
        Raises a validation error if no matching Gender is found.
        """
        if value:
            try:
                Gender.objects.get(name=value)
                return value
            except Gender.DoesNotExist:
                raise serializers.ValidationError(f"Gender '{value}' does not exist.")
        return value

    def validate(self, data):
        """
        Validate that patient first and last names, if provided, are not empty strings.
        
        Raises a validation error if either `patient_first_name` or `patient_last_name` is present but empty.
        """
        # Ensure names are not empty if provided
        first_name = data.get('patient_first_name')
        if first_name is not None and not first_name.strip():
            raise serializers.ValidationError({
                'patient_first_name': 'First name cannot be empty.'
            })
        
        last_name = data.get('patient_last_name')
        if last_name is not None and not last_name.strip():
            raise serializers.ValidationError({
                'patient_last_name': 'Last name cannot be empty.'
            })

        return data

    @transaction.atomic
    def update(self, instance, validated_data):
        """
        Updates a SensitiveMeta instance with provided data, including related center, gender, and verification state fields.
        
        Handles assignment of related Center and Gender objects by name, updates model fields, and manages verification state flags (`dob_verified`, `names_verified`) in the associated state object if provided.
        
        Returns:
            SensitiveMeta: The updated SensitiveMeta instance.
        """
        # Extract verification state data
        dob_verified = validated_data.pop('dob_verified', None)
        names_verified = validated_data.pop('names_verified', None)
        
        # Extract and handle center update
        center_name = validated_data.pop('center_name', None)
        if center_name:
            try:
                center = Center.objects.get_by_natural_key(center_name)
                instance.center = center
            except Center.DoesNotExist:
                logger.error(f"Center '{center_name}' not found during update")
                raise serializers.ValidationError(f"Center '{center_name}' does not exist.")
        
        # Extract and handle gender update
        patient_gender_name = validated_data.pop('patient_gender_name', None)
        if patient_gender_name:
            try:
                gender = Gender.objects.get(name=patient_gender_name)
                instance.patient_gender = gender
            except Gender.DoesNotExist:
                logger.error(f"Gender '{patient_gender_name}' not found during update")
                raise serializers.ValidationError(f"Gender '{patient_gender_name}' does not exist.")

        # Update regular fields using the model's update_from_dict method
        if validated_data:
            instance.update_from_dict(validated_data)
        
        # Update verification state if provided
        if dob_verified is not None or names_verified is not None:
            # Ensure state exists
            state = instance.get_or_create_state()
            
            if dob_verified is not None:
                state.dob_verified = dob_verified
                logger.info(f"Updated DOB verification for SensitiveMeta {instance.pk}: {dob_verified}")
            
            if names_verified is not None:
                state.names_verified = names_verified
                logger.info(f"Updated names verification for SensitiveMeta {instance.pk}: {names_verified}")
            
            state.save()

        return instance


