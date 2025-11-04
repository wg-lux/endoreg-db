"""
Video Examination Serializer

Serializes PatientExamination instances that are associated with VideoFile records.
This allows frontend components like VideoExaminationAnnotation.vue to display
and manage examinations within the video annotation workflow.
"""

from rest_framework import serializers

from ..models import Examination, PatientExamination, VideoFile


class VideoExaminationSerializer(serializers.ModelSerializer):
    """
    Serializer for video-based patient examinations.

    Exposes examination data within the context of video annotation:
    - Basic examination metadata (type, date, hash)
    - Related patient information (anonymized)
    - Video reference
    - Associated findings
    """

    # Custom fields for frontend compatibility
    examination_name = serializers.CharField(source="examination.name", read_only=True)
    examination_id = serializers.IntegerField(source="examination.id", read_only=True)
    video_id = serializers.IntegerField(source="video.id", read_only=True)
    patient_hash = serializers.CharField(source="patient.patient_hash", read_only=True)

    # Nested findings data
    findings = serializers.SerializerMethodField()

    class Meta:
        model = PatientExamination
        fields = [
            "id",
            "hash",
            "examination_id",
            "examination_name",
            "video_id",
            "patient_hash",
            "date_start",
            "date_end",
            "findings",
        ]
        read_only_fields = ["hash", "patient_hash"]

    def get_findings(self, obj):
        """
        Return serialized findings associated with this examination.

        Args:
            obj: PatientExamination instance

        Returns:
            List of finding dictionaries with basic metadata
        """
        patient_findings = obj.patient_findings.all()
        return [
            {
                "id": pf.id,
                "finding_id": pf.finding.id if pf.finding else None,
                "finding_name": pf.finding.name if pf.finding else None,
                "created_at": pf.created_at if hasattr(pf, "created_at") else None,
            }
            for pf in patient_findings
        ]


class VideoExaminationCreateSerializer(serializers.Serializer):
    """
    Serializer for creating video examinations via API.

    Handles the complex creation logic required to link:
    - VideoFile (must exist)
    - Examination type (must exist)
    - Patient (derived from video's SensitiveMeta)
    - New PatientExamination record
    """

    video_id = serializers.IntegerField(required=True)
    examination_id = serializers.IntegerField(required=True)
    date_start = serializers.DateField(required=False, allow_null=True)
    date_end = serializers.DateField(required=False, allow_null=True)

    def validate_video_id(self, value):
        """Ensure video exists"""
        if not VideoFile.objects.filter(id=value).exists():
            raise serializers.ValidationError(f"Video with id {value} does not exist")
        return value

    def validate_examination_id(self, value):
        """Ensure examination type exists"""
        if not Examination.objects.filter(id=value).exists():
            raise serializers.ValidationError(
                f"Examination with id {value} does not exist"
            )
        return value

    def create(self, validated_data):
        """
        Create PatientExamination record.

        Links video to examination through patient relationship:
        1. Get video and extract patient from SensitiveMeta
        2. Get examination type
        3. Create PatientExamination linking patient, examination, video

        Raises:
            ValidationError: If video has no patient or sensitive_meta
        """
        video = VideoFile.objects.get(id=validated_data["video_id"])
        examination = Examination.objects.get(id=validated_data["examination_id"])

        # Get patient from video's sensitive metadata
        if not hasattr(video, "sensitive_meta") or not video.sensitive_meta:
            raise serializers.ValidationError(
                "Video must have sensitive metadata with patient information"
            )

        sensitive_meta = video.sensitive_meta
        if not sensitive_meta.pseudo_patient:
            raise serializers.ValidationError(
                "Video's sensitive metadata must have an associated pseudo patient"
            )

        patient = sensitive_meta.pseudo_patient

        # Check if PatientExamination already exists for this video
        existing_exam = PatientExamination.objects.filter(video=video).first()
        if existing_exam:
            # Update existing
            patient_exam = existing_exam
            patient_exam.examination = examination
            if "date_start" in validated_data:
                patient_exam.date_start = validated_data["date_start"]
            if "date_end" in validated_data:
                patient_exam.date_end = validated_data["date_end"]
            patient_exam.save()
        else:
            # Create new
            patient_exam = PatientExamination.objects.create(
                patient=patient,
                examination=examination,
                video=video,
                date_start=validated_data.get("date_start"),
                date_end=validated_data.get("date_end"),
            )

        return patient_exam


class VideoExaminationUpdateSerializer(serializers.Serializer):
    """
    Serializer for updating video examinations.

    Allows modification of:
    - Examination type
    - Date range
    - Associated findings (via separate endpoint)
    """

    examination_id = serializers.IntegerField(required=False)
    date_start = serializers.DateField(required=False, allow_null=True)
    date_end = serializers.DateField(required=False, allow_null=True)

    def validate_examination_id(self, value):
        """Ensure examination type exists if provided"""
        if value is not None and not Examination.objects.filter(id=value).exists():
            raise serializers.ValidationError(
                f"Examination with id {value} does not exist"
            )
        return value

    def update(self, instance, validated_data):
        """
        Update PatientExamination fields.

        Args:
            instance: Existing PatientExamination
            validated_data: Validated update data

        Returns:
            Updated PatientExamination instance
        """
        if "examination_id" in validated_data:
            examination = Examination.objects.get(id=validated_data["examination_id"])
            instance.examination = examination

        if "date_start" in validated_data:
            instance.date_start = validated_data["date_start"]

        if "date_end" in validated_data:
            instance.date_end = validated_data["date_end"]

        instance.save()
        return instance
