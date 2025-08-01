from pathlib import Path
from rest_framework import serializers
from django.conf import settings
from ...models import RawPdfFile, SensitiveMeta


class PDFFileForMetaSerializer(serializers.ModelSerializer):
    """
    Serializer to fetch PDF metadata along with linked `SensitiveMeta` details.
    Implements validation and ensures Vue.js can process errors easily.
    """

    # Fetch patient details from `SensitiveMeta`
    patient_first_name = serializers.CharField(source="sensitive_meta.patient_first_name", read_only=True)
    patient_last_name = serializers.CharField(source="sensitive_meta.patient_last_name", read_only=True)
    patient_dob = serializers.CharField(source="sensitive_meta.patient_dob", read_only=True)
    examination_date = serializers.CharField(source="sensitive_meta.examination_date", read_only=True)

    # PDF file URL where Vue.js can fetch the document
    pdf_url = serializers.SerializerMethodField()

    # Full absolute path of the PDF file
    full_pdf_path = serializers.SerializerMethodField()

    # Direct file path from the database
    file = serializers.SerializerMethodField()

    class Meta:
        model = RawPdfFile
        fields = ['id', 'file', 'pdf_url', 'full_pdf_path', 
                  'sensitive_meta_id', 'patient_first_name', 
                  'patient_last_name', 'patient_dob', 'examination_date']

    @staticmethod
    def get_next_pdf(last_id=None):
        """
        Retrieves the first available PDF if `last_id` is NOT provided.
        Otherwise, fetches the next available PDF where `id > last_id`.
        """
        query_filter = {}
        if last_id is not None:
            try:
                query_filter = {"id__gt": int(last_id)}
            except ValueError:
                # If last_id is not a valid integer, treat it as if no ID was provided.
                # This prevents a crash and safely defaults to fetching the first PDF.
                query_filter = {}

        # Get the next available PDF
        pdf_entry = RawPdfFile.objects.select_related("sensitive_meta").filter(**query_filter).order_by('id').first()

        return pdf_entry  # Returns a model instance or None

    def get_pdf_url(self, obj):
        """
        Generates an absolute URL for accessing the PDF associated with the given object.
        
        Returns:
            The full URL as a string if the file exists; otherwise, None.
        """
        request = self.context.get('request')
        print("---------------------here :",obj.file)
        if request and obj.file:
            return request.build_absolute_uri(f"/pdf/sensitivemeta/?id={obj.id}")  # Constructs full API endpoint
        return None  # Return None if file is missing

    def get_file(self, obj):
        """
        Retrieves the relative file path of the PDF from the database.
        
        Returns:
            The relative file path as a string, or None if no file is linked.
        """
        if not obj.file:
            return None  # No file linked
        return str(obj.file.name).strip()  # Ensures clean output

    def get_full_pdf_path(self, obj):
        """
        Constructs the full absolute file path using `settings.MEDIA_ROOT`.
        """
        if not obj.file:
            return None  # No file linked

        pdf_relative_path = str(obj.file.name)

        full_path = Path(settings.MEDIA_ROOT) / pdf_relative_path

        return str(full_path) if full_path.exists() else None  # Returns path or None if file is missing

    def validate(self, data):
        """
        Validate input data to ensure a PDF file is provided and the referenced sensitive_meta_id exists.
        
        Raises:
            serializers.ValidationError: If the PDF file is missing or the sensitive_meta_id does not correspond to an existing SensitiveMeta record.
        
        Returns:
            dict: The validated input data if all checks pass.
        """
        errors = {}

        if 'file' in data and not data['file']:
            errors['file'] = "A valid PDF file is required."

        if 'sensitive_meta_id' in data and not SensitiveMeta.objects.filter(id=data['sensitive_meta_id']).exists():
            errors['sensitive_meta_id'] = "The provided sensitive_meta_id does not exist."

        if errors:
            raise serializers.ValidationError(errors)  # Returns structured error response

        return data  # Returns validated data



