from rest_framework import serializers


class SecureFileUrlSerializer(serializers.Serializer):
    """
    Serializer für sichere File-URLs mit Ablaufzeit
    """
    url = serializers.URLField()
    expires_at = serializers.DateTimeField()
    file_type = serializers.CharField(max_length=20)
    original_filename = serializers.CharField(max_length=255)
    file_size = serializers.IntegerField()

    def create(self, validated_data):
        # Nicht implementiert, da nur für Ausgabe verwendet
        raise NotImplementedError("SecureFileUrlSerializer ist nur für Ausgabe gedacht")

    def update(self, instance, validated_data):
        # Nicht implementiert, da nur für Ausgabe verwendet  
        raise NotImplementedError("SecureFileUrlSerializer ist nur für Ausgabe gedacht")