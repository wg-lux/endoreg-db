from __future__ import annotations

from endoreg_db.models.medical.patient.patient_finding import PatientFinding
from typing import TYPE_CHECKING

from rest_framework import serializers

if TYPE_CHECKING:
    _ModelSerializerMeta = serializers.ModelSerializer.Meta
else:
    _ModelSerializerMeta = object


class PatientFindingSerializer(serializers.ModelSerializer[PatientFinding]):
    class Meta(_ModelSerializerMeta):
        model = PatientFinding  # pyright: ignore[reportAssignmentType]
        # fields = '__all__'
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
