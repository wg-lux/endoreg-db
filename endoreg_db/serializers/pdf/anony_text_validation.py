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
        Selects the next RawPdfFile record, optionally starting after a given id.
        
        Parameters:
            last_id (int | str | None): If provided, finds the first RawPdfFile with id greater than this value; if omitted or None, returns the first available record.
        
        Returns:
            RawPdfFile | None: The matching RawPdfFile instance, or None if no record is found.
        """
        query_filter = {} if last_id is None else {"id__gt": int(last_id)}
        pdf_entry = RawPdfFile.objects.filter(**query_filter).order_by('id').first()
        return pdf_entry  

    def get_pdf_url(self, obj):
        """
        Builds the absolute URL for the anonymized-text PDF for the given object.
        
        Returns the absolute URL for the anonymized-text PDF endpoint using the serializer's request context, or `None` if the context has no request or the object has no associated file.
        
        Returns:
            str or None: Absolute URL string for the anonymized-text PDF, or `None` if unavailable.
        """
        request = self.context.get('request')
        return request.build_absolute_uri(f"/pdf/anony_text/?id={obj.id}") if request and obj.file else None

    def get_file(self, obj):
        """
        Return the model instance's relative file path for its stored PDF.
        
        Parameters:
            obj (RawPdfFile): Model instance containing the file field.
        
        Returns:
            str or None: The relative file path (obj.file.name) stripped of surrounding whitespace, or None if no file is associated.
        """
        return str(obj.file.name).strip() if obj.file else None  

    def get_full_pdf_path(self, obj):
        """
        Return the absolute filesystem path to the given object's file if that file exists on disk.
        
        Parameters:
            obj (RawPdfFile): Model instance with a FileField/Field-like `.file` attribute whose `.name` is a relative media path.
        
        Returns:
            str or None: Absolute path to the file under `settings.MEDIA_ROOT` if the file exists, `None` if the object has no file or the file path does not exist.
        """
        if not obj.file:
            return None
        pdf_relative_path = str(obj.file.name)
        full_path = Path(settings.MEDIA_ROOT) / pdf_relative_path
        return str(full_path) if full_path.exists() else None  

    def validate_anonymized_text(self, value):
        """
        Validate anonymized_text is non-empty and at most 5000 characters.
        
        Raises:
            serializers.ValidationError: If the text is empty after stripping whitespace or longer than 5000 characters.
        
        Returns:
            str: The validated anonymized text.
        """
        if not value.strip():
            raise serializers.ValidationError("Anonymized text cannot be empty.")
        #FIXME move this to a settings variable @Hamzaukw @maxhild
        if len(value) > 5000:  # Arbitrary limit to prevent excessively long text
            raise serializers.ValidationError("Anonymized text exceeds the maximum length of 5000 characters.")
        return value

    def update(self, instance, validated_data):
        """
        Update the instance's anonymized_text with the provided validated data.
        
        Only the 'anonymized_text' field is changed; other fields remain unchanged.
        
        Returns:
            The updated RawPdfFile instance.
        """
        instance.anonymized_text = validated_data.get('anonymized_text', instance.anonymized_text)
        instance.save()
        return instance
