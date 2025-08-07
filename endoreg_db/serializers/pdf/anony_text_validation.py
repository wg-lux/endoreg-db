from pathlib import Path
from rest_framework import serializers
from django.conf import settings
from ...models import RawPdfFile

class RawPdfAnonyTextSerializer(serializers.ModelSerializer):
    """
    Serializer to fetch PDF metadata along with `anonymized_text` from `RawPdfFile`.
    Ensures Vue.js can process JSON efficiently.
    """

    pdf_url = serializers.SerializerMethodField()
    full_pdf_path = serializers.SerializerMethodField()
    file = serializers.SerializerMethodField()

    class Meta:
        model = RawPdfFile
        fields = ['id', 'file', 'pdf_url', 'full_pdf_path', 
                  'sensitive_meta_id', 'anonymized_text']

    @staticmethod
    def get_next_pdf(last_id=None):
        """
        Retrieves the first available PDF if `last_id` is NOT provided.
        Otherwise, fetches the next available PDF where `id > last_id`.
        """
        query_filter = {} if last_id is None else {"id__gt": int(last_id)}
        pdf_entry = RawPdfFile.objects.filter(**query_filter).order_by('id').first()
        return pdf_entry  

    def get_pdf_url(self, obj):
        """
        Returns the absolute URL for accessing the anonymized text PDF endpoint for the given object.
        
        If the request context or file is missing, returns None.
        """
        request = self.context.get('request')
        return request.build_absolute_uri(f"/pdf/anony_text/?id={obj.id}") if request and obj.file else None

    def get_file(self, obj):
        """
        Retrieves the relative file path of the PDF from the model instance.
        
        Returns:
            The relative file path as a string, or None if no file is associated.
        """
        return str(obj.file.name).strip() if obj.file else None  

    def get_full_pdf_path(self, obj):
        """
        Constructs the full absolute file path using `settings.MEDIA_ROOT`.
        """
        if not obj.file:
            return None
        pdf_relative_path = str(obj.file.name)
        full_path = Path(settings.MEDIA_ROOT) / pdf_relative_path
        return str(full_path) if full_path.exists() else None  

    def validate_anonymized_text(self, value):
        """
        Validates that the anonymized text is non-empty and does not exceed 5000 characters.
        
        Raises:
            serializers.ValidationError: If the text is empty or exceeds the length limit.
        """
        if not value.strip():
            raise serializers.ValidationError("Anonymized text cannot be empty.")
        #FIXME move this to a settings variable @Hamzaukw @maxhild
        if len(value) > 5000:  # Arbitrary limit to prevent excessively long text
            raise serializers.ValidationError("Anonymized text exceeds the maximum length of 5000 characters.")
        return value

    def update(self, instance, validated_data):
        """
        Update the `anonymized_text` field of a RawPdfFile instance with validated data.
        
        Only the `anonymized_text` field is modified; all other fields remain unchanged.
        
        Returns:
            The updated RawPdfFile instance.
        """
        instance.anonymized_text = validated_data.get('anonymized_text', instance.anonymized_text)
        instance.save()
        return instance

