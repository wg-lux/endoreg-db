from rest_framework import serializers
from endoreg_db.models import SensitiveMeta, LabelVideoSegment, Video, Examination, ExaminationType, Center, EndoscopyProcessor, Patient, PatientExamination

class SensitiveMetaSerializer(serializers.ModelSerializer):
    """Serializer for sensitive metadata."""
    pseudo_examination = serializers.SerializerMethodField()

    def get_pseudo_examination(self, obj:SensitiveMeta):
        """Return the pseudo examination hash."""
        return obj.pseudo_examination.hash if obj.pseudo_examination else None

    class Meta:
        model = SensitiveMeta
        fields = [
            "center", # probably remove later
            "examination_date",
            "pseudo_patient",
            "patient_gender",
            "examiners",
            "pseudo_examination",
            "examination_hash",
            "patient_hash",
            "endoscope_type",
            "endoscope_sn",
            "state_verified",
            "state_names_substituted",
            "state_dob_substituted",
            "state_examiners_substituted",
        ]
