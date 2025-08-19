from endoreg_db.models import PatientFinding
from rest_framework import serializers

class PatientFindingSerializer(serializers.ModelSerializer):
    class Meta:
        model = PatientFinding
        #fields = '__all__'
        fields = [
            "id",
            "patient_examination",
            "finding",
            # relationships (kept for backward compatibility)
            "video_segments",
            "interventions",
            "classifications",
            # timestamps are generally safe/expected by clients
            "created_at",
            "updated_at",
            # expose active state, but NOT who/when deactivated
            "is_active",
        ]
        read_only_fields = [
            "id",
            "created_at",
            "updated_at",
            "is_active",
            # relationships are usually read-only here
            "video_segments",
            "interventions",
            "classifications",
        ]
