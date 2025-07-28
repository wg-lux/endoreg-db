from endoreg_db.models import Examination


from rest_framework import serializers


class ExaminationDropdownSerializer(serializers.ModelSerializer):
    """Serializer für Examination-Dropdown"""
    display_name = serializers.SerializerMethodField()

    class Meta:
        model = Examination
        fields = ['id', 'name', 'display_name']

    def get_display_name(self, obj):
        """Gibt eine benutzerfreundliche Anzeige für die Untersuchung zurück"""
        return obj.name