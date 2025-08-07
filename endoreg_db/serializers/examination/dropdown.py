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
        Return a user-friendly display name for the examination.
        
        If the German name (`name_de`) is available, it is returned; otherwise, the default name is used.
        """

        return obj.name

