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
        """
        Raises NotImplementedError to indicate that instance creation is not supported for this serializer.
        """
        raise NotImplementedError("SecureFileUrlSerializer ist nur für Ausgabe gedacht")

    def update(self, instance, validated_data):
        # Nicht implementiert, da nur für Ausgabe verwendet  
        """
        Raises NotImplementedError to indicate that updating is not supported, as this serializer is intended for output only.
        """
        raise NotImplementedError("SecureFileUrlSerializer ist nur für Ausgabe gedacht")