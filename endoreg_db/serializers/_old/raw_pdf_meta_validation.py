from pathlib import Path
from rest_framework import serializers
from django.conf import settings
#from ...models import RawPdfFile, SensitiveMeta
from endoreg_db.models import RawPdfFile, SensitiveMeta

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
        query_filter = {} if last_id is None else {"id__gt": int(last_id)}

        # Get the next available PDF
        pdf_entry = RawPdfFile.objects.select_related("sensitive_meta").filter(**query_filter).order_by('id').first()

        return pdf_entry  # Returns a model instance or None

    def get_pdf_url(self, obj):
        """
        Generates the full URL for Vue.js to fetch and display the PDF.
        """
        request = self.context.get('request')
        print("---------------------here :",obj.file)
        if request and obj.file:
            return request.build_absolute_uri(f"/api/pdf/sensitivemeta/?id={obj.id}")  # Constructs full API endpoint
        return None  # Return None if file is missing

    def get_file(self, obj):
        """
        Returns the relative file path stored in the database.
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
        Ensures that the PDF file is valid and has required fields.
        """
        errors = {}

        if 'file' in data and not data['file']:
            errors['file'] = "A valid PDF file is required."

        if 'sensitive_meta_id' in data and not SensitiveMeta.objects.filter(id=data['sensitive_meta_id']).exists():
            errors['sensitive_meta_id'] = "The provided sensitive_meta_id does not exist."

        if errors:
            raise serializers.ValidationError(errors)  # Returns structured error response

        return data  # Returns validated data


from rest_framework import serializers
#from ..models import SensitiveMeta

class SensitiveMetaUpdateSerializer(serializers.ModelSerializer):
    """
    Serializer for updating patient details in the `SensitiveMeta` table.
    Allows partial updates.
    """

    sensitive_meta_id = serializers.IntegerField(write_only=True)  # Needed for lookup, not included in response

    class Meta:
        model = SensitiveMeta
        fields = ['sensitive_meta_id', 'patient_first_name', 'patient_last_name', 'patient_dob', 'examination_date']

    def validate(self, data):
        """
        Ensures valid input before updating.
        """
        errors = {}

        if 'patient_first_name' in data and (not data['patient_first_name'].strip()):
            errors['patient_first_name'] = "First name cannot be empty."

        if 'patient_last_name' in data and (not data['patient_last_name'].strip()):
            errors['patient_last_name'] = "Last name cannot be empty."

        if 'patient_dob' in data and not data['patient_dob']:
            errors['patient_dob'] = "Date of birth is required."

        if 'examination_date' in data and not data['examination_date']:
            errors['examination_date'] = "Examination date is required."

        if errors:
            raise serializers.ValidationError(errors)

        return data

    def update(self, instance, validated_data):
        """
        Updates only the provided fields dynamically.
        """
        validated_data.pop("sensitive_meta_id", None)  # Remove ID before updating

        for attr, value in validated_data.items():
            setattr(instance, attr, value)  # Dynamically update fields

        instance.save()
        return instance



"""
await import('https://cdn.jsdelivr.net/npm/axios/dist/axios.min.js');
const fetchPdfMeta = async (lastId = 1) => {
    const url = lastId 
        ? `http://localhost:8000/api/pdf/sensitivemeta/?last_id=${lastId}` 
        : "http://localhost:8000/api/pdf/sensitivemeta/";

    try {
        const response = await axios.get(url);
        console.log("PDF Metadata:", response.data);
    } catch (error) {
        console.error("Error fetching PDFs:", error.response?.data || error);
    }
};

fetchPdfMeta(); // Fetch the first available PDF

"""

"""with header response"""
"""
await import('https://cdn.jsdelivr.net/npm/axios/dist/axios.min.js');

const fetchPdfMeta = async (lastId = 1) => {
    const url = lastId 
        ? `http://localhost:8000/api/pdf/sensitivemeta/?last_id=${lastId}` 
        : "http://localhost:8000/api/pdf/sensitivemeta/";

    try {
        const response = await axios.get(url, {
            headers: {
                "Accept": "application/json"  
            }
        });

        console.log("PDF Metadata (JSON Expected):", response.data);
    } catch (error) {
        console.error("Error fetching PDFs:", error.response?.data || error);
    }
};

fetchPdfMeta(); 
"""


"""
await import('https://cdn.jsdelivr.net/npm/axios/dist/axios.min.js');

const updatePatientInfo = async () => {
    const updatedData = {
        sensitive_meta_id: 1,
        patient_first_name: "dummy value",
        patient_last_name: "dummy value",
        patient_dob: "1994-06-15",
        examination_date: "2024-06-15"
    };

    try {
        const response = await axios.patch("http://localhost:8000/api/pdf/update_sensitivemeta/", updatedData, {
            headers: { "Content-Type": "application/json" }
        });

        console.log("Update Success:", response.data);
        alert("Patient information updated successfully!");

        return response.data;
    } catch (error) {
        console.error("Update Error:", error.response?.data || error);
        alert("Failed to update patient information.");
        return error.response?.data || { error: "Unknown error" };
    }
};

updatePatientInfo().then(response => console.log("Final Response:", response));

"""
