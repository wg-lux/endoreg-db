from rest_framework import serializers
from rest_framework import serializers
from endoreg_db.models import (
    PatientExamination, Patient, Examination, Finding, PatientFinding, PatientFindingLocation,
    FindingLocationClassificationChoice, FindingLocationClassification, FindingMorphologyClassification, 
    FindingMorphologyClassificationChoice, PatientFindingMorphology, PatientFinding, PatientFindingMorphology
)

class PatientExaminationSerializer(serializers.ModelSerializer):
    """
    Serializer for handling Patient + Examination selection.
    """
    patient_id = serializers.PrimaryKeyRelatedField(
        queryset=Patient.objects.all(), source="patient", write_only=True
    )
    examination_id = serializers.PrimaryKeyRelatedField(
        #queryset=Examination.objects.all(), source="examination", write_only=True #for all values
        queryset=Examination.objects.filter(id=1), source="examination", #write_only=True

    )
    examination_name = serializers.CharField(source='examination.name', read_only=True)  # Show name in response


    # for finding name = colon_polyp
    finding_name = serializers.SerializerMethodField()
    #finding_id = None
    finding_id = serializers.IntegerField(read_only=True)  # Store finding_id in the serializer

     # Fetching Finding Location Classification (for colonoscopy_default)
    finding_location_classification_id = serializers.SerializerMethodField()
    finding_location_choices = serializers.SerializerMethodField()

    #User-selected location choice
    location_choice_id = serializers.IntegerField(write_only=True, required=False)

    class Meta:
        model = PatientExamination
        fields = [
            'id', 'patient_id', 'examination_id', 'examination_name',
            'date_start', 'date_end', 'finding_id', 'finding_name',
            'finding_location_classification_id', 'finding_location_choices', 'location_choice_id'
        ]


    def get_finding_name(self, obj):
        """
        Returns the name of the Finding where name='colon_polyp' and id=1.
        """
        finding = Finding.objects.filter(name="colon_polyp", id=1).first()
        
        if finding:
            #  Save `finding_id` so it is accessible in views
            self.context['finding_id'] = finding.id
            return finding.name
        
        return None
    
    def get_finding_location_classification_id(self, obj):
        """ Fetch the ID of FindingLocationClassification where name='colonoscopy_default' """
        classification = FindingLocationClassification.objects.filter(name="colonoscopy_default").first()
        if classification:
            return classification.id
        return None

    def get_finding_location_choices(self, obj):
        """
        Fetches FindingLocationClassificationChoice records
        that are linked to `FindingLocationClassification` where name='colonoscopy_default'.
        """
        classification = FindingLocationClassification.objects.filter(name="colonoscopy_default").first()
        if classification:
            choices = classification.choices.all()
            return [{"id": choice.id, "name": choice.name} for choice in choices]
        return []

    '''def create(self, validated_data):
        """
        Create a new PatientExamination instance and store `finding_id` correctly.
        """
        #  Create the `PatientExamination` record properly
        patient_examination = PatientExamination.objects.create(**validated_data)

        # Retrieve and store `finding_id` from context
        #self._context['finding_id'] = self._context.get('finding_id', None)
        finding = Finding.objects.filter(name="colon_polyp", id=1).first()
        print("here is finding:",finding)
        if finding:
            print("here i am")
            self.finding_id = finding.id 

        return patient_examination'''
    
    def create(self, validated_data):
        """
        Create a new PatientExamination instance and store `finding_id` correctly.
        """
        print("Received validated_data:", validated_data)  #  Debugging line

        #  Create `PatientExamination`
        patient_examination = PatientExamination.objects.create(**validated_data)

        #  Debugging to check if it was created
        print("Created patient_examination:", patient_examination)

        #  Retrieve and store `finding_id`
        finding = Finding.objects.filter(name="colon_polyp", id=1).first()
        print("here is finding:", finding)
        if finding:
            print("here i am")
            self.finding_id = finding.id 

        return patient_examination


    ''' def create(self, validated_data):
        
        patient = validated_data.get('patient')
        examination = validated_data.get('examination')
        date_start = validated_data.get('date_start', None)
        date_end = validated_data.get('date_end', None)
       
        # Save new PatientExamination entry
        patient_examination = PatientExamination.objects.create(
            patient=patient,
            examination=examination,
            date_start=date_start,
            date_end=date_end
        )
        return patient_examination'''
    

    

# patient finding serializers
class PatientFindingSerializer(serializers.ModelSerializer):
    """this isto handling patientfinding entries,"""
    # need to update in future if we remove this table
    class Meta:
        model = PatientFinding
        fields = ['id', 'finding', 'patient_examination']




class PatientFindingLocationSerializer(serializers.ModelSerializer):
    """
    Serializer for handling `PatientFindingLocation` entries.
    """

    location_classification = serializers.PrimaryKeyRelatedField(
        queryset=FindingLocationClassification.objects.all(),
        write_only=True
    )

    location_choice = serializers.PrimaryKeyRelatedField(
        queryset=FindingLocationClassificationChoice.objects.all(),
        write_only=True
    )

    class Meta:
        model = PatientFindingLocation
        fields = ['id', 'location_classification', 'location_choice', 'subcategories', 'numerical_descriptors']



class FindingMorphologyClassificationSerializer(serializers.ModelSerializer):
    """
    Serializer to fetch all FindingMorphologyClassification entries.
    """
    class Meta:
        model = FindingMorphologyClassification
        fields = ['id', 'name']  # Fetch only ID and name

class FindingMorphologyClassificationChoiceSerializer(serializers.ModelSerializer):
    """
    Serializer to fetch FindingMorphologyClassificationChoice based on classification selection.
    """
    class Meta:
        model = FindingMorphologyClassificationChoice
        fields = ['id', 'name', 'classification']

class PatientFindingMorphologySerializer(serializers.ModelSerializer):
    """
    Serializer to handle saving of selected FindingMorphologyClassification
    and FindingMorphologyClassificationChoice in PatientFindingMorphology.
    """
    morphology_classification_id = serializers.PrimaryKeyRelatedField(
        queryset=FindingMorphologyClassification.objects.all(), 
        source='morphology_classification',
        write_only=True
    )
    
    morphology_choice_id = serializers.PrimaryKeyRelatedField(
        queryset=FindingMorphologyClassificationChoice.objects.all(),
        source='morphology_choice',
        write_only=True
    )

    morphology_classification_name = serializers.CharField(
        source='morphology_classification.name', read_only=True
    )
    
    morphology_choice_name = serializers.CharField(
        source='morphology_choice.name', read_only=True
    )

    class Meta:
        model = PatientFindingMorphology
        fields = [
            'id',
            'morphology_classification_id',
            'morphology_choice_id',
            'morphology_classification_name',
            'morphology_choice_name'
        ]
    
    def validate(self, data):
        """
        Ensure that the selected morphology_choice belongs to the selected morphology_classification.
        """
        classification = data.get('morphology_classification')
        choice = data.get('morphology_choice')

        if choice.classification != classification:
            raise serializers.ValidationError({
                "morphology_choice_id": "The selected choice does not belong to the selected classification."
            })
        
        return data



class PatientFindingMorphologyRelationSerializer(serializers.ModelSerializer):
    """
    Serializer for storing the many-to-many relationship between 
    `PatientFinding` and `PatientFindingMorphology`.
    """

    patientfinding_id = serializers.PrimaryKeyRelatedField(  
        queryset=PatientFinding.objects.all(),
        write_only=True
    )
    
    patientfindingmorphology_id = serializers.PrimaryKeyRelatedField(  
        queryset=PatientFindingMorphology.objects.all(),
        write_only=True
    )

    class Meta:
        model = PatientFinding.morphologies.through  
        fields = ["patientfinding_id", "patientfindingmorphology_id"]


class FindingInterventionSerializer(serializers.ModelSerializer):
    """
    Serializer to fetch all FindingMorphologyClassification entries.
    """
    class Meta:
        model = FindingMorphologyClassification
        fields = ['id', 'name']  # Fetch only ID and name