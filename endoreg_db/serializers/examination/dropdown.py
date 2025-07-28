from endoreg_db.models import Examination


from rest_framework import serializers


class ExaminationDropdownSerializer(serializers.ModelSerializer):
    """Serializer für Examination-Dropdown"""
    display_name = serializers.SerializerMethodField()

    class Meta:
        model = Examination
        fields = ['id', 'name', 'name_de', 'name_en', 'display_name']

    def get_display_name(self, obj):
        """
        Return a user-friendly display name for the examination.
        
        If the German name (`name_de`) is available, it is returned; otherwise, the default name is used.
        """
        return obj.name_de if obj.name_de else obj.name