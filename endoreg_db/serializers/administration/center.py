from rest_framework import serializers
from endoreg_db.models import Center


class CenterSerializer(serializers.ModelSerializer):
    """Serializer für Center-Modell"""

    center_key = serializers.CharField(read_only=True)
    name = serializers.CharField(source="display_name", read_only=True)

    class Meta:
        model = Center
        fields = [
            "id",
            "center_key",
            "name",
            "display_name",
        ]
        read_only_fields = ["id", "center_key", "name", "display_name"]
