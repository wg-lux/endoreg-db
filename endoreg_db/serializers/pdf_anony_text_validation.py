from pathlib import Path
from rest_framework import serializers
from django.conf import settings
from ..models import RawPdfFile

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
        Generates the full URL where Vue.js can fetch and display the PDF.
        """
        request = self.context.get('request')
        return request.build_absolute_uri(f"/pdf/anony_text/?id={obj.id}") if request and obj.file else None

    def get_file(self, obj):
        """
        Returns the relative file path stored in the database.
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
        Ensures the anonymized_text is not empty or too long.
        """
        if not value.strip():
            raise serializers.ValidationError("Anonymized text cannot be empty.")
        #FIXME move this to a settings variable @Hamzaukw @maxhild
        if len(value) > 5000:  # Arbitrary limit to prevent excessively long text
            raise serializers.ValidationError("Anonymized text exceeds the maximum length of 5000 characters.")
        return value

    def update(self, instance, validated_data):
        """
        Updates only `anonymized_text` without modifying other fields.
        """
        instance.anonymized_text = validated_data.get('anonymized_text', instance.anonymized_text)
        instance.save()
        return instance



"""
await import('https://cdn.jsdelivr.net/npm/axios/dist/axios.min.js');

const fetchPdfWithAnonymizedText = async (lastId = null) => {
    const url = lastId 
        ? `http://localhost:8000/pdf/anony_text/?last_id=${lastId}` 
        : "http://localhost:8000/pdf/anony_text/";

    try {
        const response = await axios.get(url, { headers: { "Accept": "application/json" } });
        console.log("PDF Data:", response.data);
    } catch (error) {
        console.error("Error fetching PDF:", error.response?.data || error);
    }
};

fetchPdfWithAnonymizedText();

"""

"""
await import('https://cdn.jsdelivr.net/npm/axios/dist/axios.min.js');

const updateAnonymizedText = async (pdfId, newText) => {
    try {
        const response = await axios.patch("http://localhost:8000/pdf/update_anony_text/", {
            id: pdfId,
            anonymized_text: newText
        }, { headers: { "Content-Type": "application/json" } });

        console.log("Update Success:", response.data);
    } catch (error) {
        console.error("Update Error:", error.response?.data || error);
    }
};

updateAnonymizedText(1, "Updated anonymized text.");

"""

"""
await import('https://cdn.jsdelivr.net/npm/axios/dist/axios.min.js');

const updateAnonymizedText = async () => {
    const updatedData = {
        id: 1,
        anonymized_text: "This is the updated anonymized text."
    };

    try {
        const response = await axios.patch("http://localhost:8000/pdf/update_anony_text/", updatedData, {
            headers: { "Content-Type": "application/json" }
        });

        console.log("Update Success:", response.data);
        alert("Anonymized text updated successfully!");
    } catch (error) {
        console.error("Update Error:", error.response?.data || error);
        alert("Failed to update anonymized text.");
    }
};

updateAnonymizedText();
"""