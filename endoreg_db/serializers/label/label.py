from endoreg_db.models import Label


from rest_framework import serializers


class LabelSerializer(serializers.ModelSerializer):
    """
    Serializer for fetching labels from the `endoreg_db_label` table.
    Includes `id` (for backend processing) and `name` (for dropdown display in Vue.js).
    """

    class Meta:
        model = Label
        fields = ["id", "name"]