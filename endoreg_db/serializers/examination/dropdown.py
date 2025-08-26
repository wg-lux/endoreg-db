from endoreg_db.models import Examination


from rest_framework import serializers


class ExaminationDropdownSerializer(serializers.ModelSerializer):
    """Serializer für Examination-Dropdown"""
    display_name = serializers.SerializerMethodField()

    class Meta:
        model = Examination
        fields = ['id', 'name', 'display_name']

    def get_display_name(self, obj):
            
            """
            Return a user-friendly (localized) display name for the examination.
            Prefers a German translation (`name_de`) when available; otherwise falls back to `name`.   """
            return getattr(obj, "name_de", None) or obj.name

