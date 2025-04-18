from rest_framework import serializers
from endoreg_db.models import LabelVideoSegment, Video, PatientExamination

class ExportLabelVideoSegmentSerializer(serializers.ModelSerializer):
    """Serializer for LabelVideoSegment representation."""
    class Meta:
        model = LabelVideoSegment
        fields = [
            "prediction_meta", # Foreign key to VideoPredictionMeta
            "patient_findings", # ManyToMany field to PatientFinding
            "start_frame_number",
            "end_frame_number",
            "source",
            "label",

        ]

class ExportVideoSerializer(serializers.ModelSerializer):
    """Serializer for Video representation."""
    patient = ExportPatientSerializer(read_only=True)
    examination = ExportExaminationSerializer(read_only=True)
    center = ExportCenterSerializer(read_only=True)

    sensitive_meta = export_sensitive_meta = ExportSensitiveMetaSerializer(read_only=True)
    
    file = serializers.SerializerMethodField()

    class Meta:
        model = Video
        fields = [
            'file',             # Handled by get_file
            'patient',          # Serialized as ID
            'examination',      # Serialized as ID
            'date',             # Direct field
            'duration',         # Direct field
            'height',           # Direct field
            'width',            # Direct field
            'endoscope_image_x', # Direct field
            'endoscope_image_y', # Direct field
            'endoscope_image_width', # Direct field
            'endoscope_image_height',# Direct field
            'center',           # Serialized as name
            'processor',        # Serialized as name
            'video_meta',       # Nested serializer
            # 'predictions',      # Direct JSON field
            'fps',    
            "frame_count",          # Direct field
            # Add 'uuid' if you need a unique identifier for the video itself
            'uuid',
        ]

    def get_file(self, obj):
        """Return the absolute path of the video file."""
        if obj.file:
            try:
                # Attempt to get the absolute path
                return obj.file.path
            except NotImplementedError:
                # Fallback for storage systems that don't support path
                return obj.file.name
        return None


    

class ExportPatientExaminationSerializer(serializers.ModelSerializer):
    """Serializer for PatientExamination representation."""
    patient = ExportPatientSerializer(read_only=True)
    examination = ExportExaminationSerializer(read_only=True)
    video = ExportVideoSerializer(read_only=True)

    class Meta:
        model = PatientExamination
        fields = [
            "patient",
            "examination",
            "video",            
            'date_start', 'date_end', 'hash']

    center = ExportCenterSerializer(read_only=True)
    processor = ExportProcessorSerializer(read_only=True)

   