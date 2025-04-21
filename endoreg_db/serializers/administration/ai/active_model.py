from rest_framework import serializers
from endoreg_db.models import ActiveModel

class ActiveModelSerializer(serializers.ModelSerializer):
    """
    Serializer for the ActiveModel model.
    """
    class Meta:
        model = ActiveModel
        fields = '__all__'
