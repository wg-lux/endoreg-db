from endoreg_db.models import VideoFile


from rest_framework import serializers


class VideoBriefSerializer(serializers.ModelSerializer):
    class Meta:
        model  = VideoFile
        fields = ["id", "original_file_name", "sensitive_meta_id"]  # for tables/overview