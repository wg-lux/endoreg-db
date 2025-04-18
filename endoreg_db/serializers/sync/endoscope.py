from rest_framework import serializers
from endoreg_db.models import Endoscope

class EndoscopeSerializer(serializers.ModelSerializer):
    """Serializer for Endoscope representation."""

    def __init__(self, *args, **kwargs):
        # Pop custom arguments *before* calling super
        endoscope_full = kwargs.pop("endoscope_full", False)

        # Call super().__init__ *after* popping custom args
        super().__init__(*args, **kwargs)

        # Conditionally set the 'endoscope' field *after* initialization
        if endoscope_full:
            self.fields["endoscope"] = EndoscopeSerializer()
        else:
            self.fields["endoscope"] = serializers.PrimaryKeyRelatedField(
                queryset=Endoscope.objects.all(),
            )

    class Meta:
        model = Endoscope
        fields = "__all__"

    
