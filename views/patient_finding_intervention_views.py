from rest_framework import viewsets
from rest_framework.response import Response
from rest_framework.decorators import api_view
from endoreg_db.models import Patient, Examination, Finding ,PatientExamination, PatientFinding, FindingLocationClassification,PatientFindingLocation
from endoreg_db.serializers.patient import PatientSerializer
from endoreg_db.serializers.examination import ExaminationSerializer
from endoreg_db.serializers.patient_finding_interventions import PatientExaminationSerializer, PatientFindingSerializer
from rest_framework.decorators import api_view
from rest_framework.response import Response
from rest_framework import status
from django.db import transaction
from endoreg_db.models import PatientExamination, PatientFindingIntervention,FindingIntervention
from endoreg_db.serializers.patient_finding_interventions import (
    PatientExaminationSerializer, FindingInterventionSerializer,
    PatientFindingSerializer,PatientFindingLocationSerializer,FindingMorphologyClassification,FindingMorphologyClassificationSerializer,
    PatientExaminationSerializer, PatientFindingSerializer, PatientFindingMorphologySerializer,PatientFindingMorphologyRelationSerializer
)
from endoreg_db.models import (
    Patient, Examination, Finding,
    PatientExamination, PatientFinding, PatientFindingLocation,
    FindingLocationClassification, FindingLocationClassificationChoice
)


@api_view(['GET']) # no need for JsonResponse()with api_view(['Get'])
def get_all_patients(request):

    patients = Patient.objects.all()  # Fetch patients from DB
    serializer = PatientSerializer(patients, many=True)  # Serialize data
    return Response(serializer.data)  # Return JSON response

@api_view(['GET'])
def get_all_examinations(request):
    """
    API to fetch all examinations.
    """
    examinations = Examination.objects.all()  # Fetch examinations from DB
    serializer = ExaminationSerializer(examinations, many=True)  # Serialize data
    return Response(serializer.data)  # Return JSON response

@api_view(['GET'])
def get_colon_polyp_finding(request):
    """
    API to fetch the Finding where name='colon_polyp' and id=1.
    """
    finding = Finding.objects.filter(name="colon_polyp", id=1).first()
    if finding:
        return Response({"id": finding.id, "name": finding.name}, status=status.HTTP_200_OK)
    return Response({"error": "Finding not found"}, status=status.HTTP_404_NOT_FOUND)

'''@api_view(['GET'])
def get_colonoscopy_default_location(request):
    """
    API to fetch FindingLocationClassification where name="colonoscopy_default".
    """
    location = FindingLocationClassification.objects.filter(name="colonoscopy_default").first()
    if location:
        serializer = FindingLocationClassificationSerializer(location)
        return Response(serializer.data, status=status.HTTP_200_OK)
    return Response({"error": "Location classification not found"}, status=status.HTTP_404_NOT_FOUND)'''


@api_view(['GET'])
def get_finding_location_classification(request):
    """
    API to fetch FindingLocationClassification where name='colonoscopy_default'
    along with its related choices.
    """
    classification = FindingLocationClassification.objects.filter(name="colonoscopy_default").first()
    
    if classification:
        choices = classification.choices.all()
        response_data = {
            "id": classification.id,
            "name": classification.name,
            "choices": [{"id": choice.id, "name": choice.name} for choice in choices]
        }
        return Response(response_data, status=status.HTTP_200_OK)
    
    return Response({"error": "Finding Location Classification not found"}, status=status.HTTP_404_NOT_FOUND)

@api_view(['GET'])
def get_all_morphology_classifications(request):
    """
    Fetch all FindingMorphologyClassification entries.
    """
    classifications = FindingMorphologyClassification.objects.all()
    serializer = FindingMorphologyClassificationSerializer(classifications, many=True)
    return Response(serializer.data, status=status.HTTP_200_OK)

@api_view(['GET'])
def get_all_interventions(request):
    """
    Fetch all Findingintervention entries
    """
    classifications = FindingIntervention.objects.all()
    serializer = FindingInterventionSerializer(classifications, many= True)
    return Response(serializer.data, status=status.HTTP_200_OK)



@api_view(['POST'])
def final_submit(request):
    """
    API to save:
     - `PatientExamination`
     - `PatientFinding` (if `finding_id` exists)
     - `FindingLocationClassification` (colonoscopy_default)
     -  Now working on  `PatientFindingLocation` (if `location_choice_id` is provided)
     - `PatientFindingMorphology` (if `morphology_classification_id` & `morphology_choice_id` are provided)
     - `PatientFinding_Morphology' relationship (links `PatientFinding` to `PatientFindingMorphology`)
     -  Now working on 'Intervention' ...... 
     - Ensures all data validation before saving
    """
    
    with transaction.atomic():
        # Extract additional fields from request
        location_choice_id = request.data.pop("location_choice_id", None)
        morphology_classification_id = request.data.pop("morphology_classification_id", None)
        morphology_choice_id = request.data.pop("morphology_choice_id", None)

        # Save PatientExamination using Serializer
        patient_examination_serializer = PatientExaminationSerializer(data=request.data)

        if patient_examination_serializer.is_valid():
            patient_examination = patient_examination_serializer.save()
            print(" Saved PatientExamination:", patient_examination)

            # Retrieve `finding_id` (Optional)
            finding_id = patient_examination_serializer.finding_id  
            patient_finding_instance = None  

            if finding_id:
                finding = Finding.objects.filter(id=finding_id).first()

                if finding:
                    # Save PatientFinding
                    patient_finding_serializer = PatientFindingSerializer(data={
                        "patient_examination": patient_examination.id,
                        "finding": finding.id
                    })
                    
                    if patient_finding_serializer.is_valid():
                        patient_finding_instance = patient_finding_serializer.save()
                        print(" Saved PatientFinding:", patient_finding_instance)
                    else:
                        return Response(patient_finding_serializer.errors, status=status.HTTP_400_BAD_REQUEST)

            # Fetch FindingLocationClassification (Default: colonoscopy_default)
            classification = FindingLocationClassification.objects.filter(name="colonoscopy_default").first()
            classification_id = classification.id if classification else None

            print("-----------------------------------------------1-------------------------")

            # Handle `PatientFindingLocation`
            patient_finding_location_instance = None
            if location_choice_id and classification and patient_finding_instance:
                location_choice = classification.choices.filter(id=location_choice_id).first()

                if location_choice:
                    patient_finding_location_serializer = PatientFindingLocationSerializer(data={
                        "location_classification": classification.id,
                        "location_choice": location_choice.id,
                        #"subcategories": location_choice.subcategories,
                        #"numerical_descriptors": location_choice.numerical_descriptors
                    })

                    if patient_finding_location_serializer.is_valid():
                        patient_finding_location_instance = patient_finding_location_serializer.save()
                        patient_finding_instance.locations.add(patient_finding_location_instance)
                        patient_finding_instance.save()
                        print(" Linked PatientFindingLocation to PatientFinding")
                    else:
                        return Response(patient_finding_location_serializer.errors, status=status.HTTP_400_BAD_REQUEST)

            #  Save Morphology Data
            patient_finding_morphology_instance = None
            if morphology_classification_id and morphology_choice_id:
                patient_finding_morphology_serializer = PatientFindingMorphologySerializer(data={
                    "morphology_classification_id": morphology_classification_id,
                    "morphology_choice_id": morphology_choice_id
                })

                if patient_finding_morphology_serializer.is_valid():
                    patient_finding_morphology_instance = patient_finding_morphology_serializer.save()
                    print("Saved PatientFindingMorphology:", patient_finding_morphology_instance)
                else:
                    return Response(patient_finding_morphology_serializer.errors, status=status.HTTP_400_BAD_REQUEST)

            # Save Relationship in `PatientFinding_Morphology`
            if patient_finding_instance and patient_finding_morphology_instance:
                pivot_model = PatientFinding.morphologies.through  
                relation_instance = pivot_model.objects.create(  #  Use .create() directly
                    patientfinding_id=patient_finding_instance.id,  
                    patientfindingmorphology_id=patient_finding_morphology_instance.id  
                )
                print(" here i am ---- : ---- Linked PatientFindingMorphology to PatientFinding")

            

            #  Return Final Response to see in console
            return Response(
                {
                    "message": "Data saved successfully",
                    "patient_examination_id": patient_examination.id,
                    "finding_id": finding_id,
                    "patient_finding_id": patient_finding_instance.id if patient_finding_instance else None,
                    "finding_location_classification_id": classification_id,
                    "selected_location_choice_id": location_choice_id if patient_finding_location_instance else None,
                    "patient_finding_location_id": patient_finding_location_instance.id if patient_finding_location_instance else None,
                    "morphology_classification_id": morphology_classification_id if patient_finding_morphology_instance else None,
                    "morphology_choice_id": morphology_choice_id if patient_finding_morphology_instance else None,
                    "patientfindingmorphology_relation_id": relation_instance.id if relation_instance else None
                },
                status=status.HTTP_201_CREATED
            )

        return Response(patient_examination_serializer.errors, status=status.HTTP_400_BAD_REQUEST)



'''@api_view(['POST'])
def final_submit(request):
    """
    API to save:
     - `PatientExamination`
     - `PatientFinding` (if `finding_id` exists)
     - `FindingLocationClassification` (colonoscopy_default)
     - `PatientFindingLocation` (if `location_choice_id` is provided)
     - `PatientFindingMorphology` (if `morphology_classification_id` & `morphology_choice_id` are provided)
     - Ensures all data validation before saving
    """
    
    with transaction.atomic():
        # Extract fields separately before passing to serializers
        location_choice_id = request.data.pop("location_choice_id", None)
        morphology_classification_id = request.data.pop("morphology_classification_id", None)
        morphology_choice_id = request.data.pop("morphology_choice_id", None)

        # Save PatientExamination using Serializer
        patient_examination_serializer = PatientExaminationSerializer(data=request.data)

        if patient_examination_serializer.is_valid():
            patient_examination = patient_examination_serializer.save()
            print(" Saved PatientExamination:", patient_examination)

            # Retrieve `finding_id` (Optional)
            finding_id = patient_examination_serializer.finding_id  
            patient_finding_instance = None  

            if finding_id:
                finding = Finding.objects.filter(id=finding_id).first()

                if finding:
                    # Save `PatientFinding`
                    patient_finding_serializer = PatientFindingSerializer(data={
                        "patient_examination": patient_examination.id,
                        "finding": finding.id
                    })
                    
                    if patient_finding_serializer.is_valid():
                        patient_finding_instance = patient_finding_serializer.save()
                        print("Saved PatientFinding:", patient_finding_instance)
                    else:
                        return Response(patient_finding_serializer.errors, status=status.HTTP_400_BAD_REQUEST)

            # Fetch FindingLocationClassification
            classification = FindingLocationClassification.objects.filter(name="colonoscopy_default").first()
            classification_id = classification.id if classification else None
            classification_choices = (
                [{"id": choice.id, "name": choice.name} for choice in classification.choices.all()]
                if classification else []
            )

            # Handle `PatientFindingLocation`
            patient_finding_location_instance = None
            if location_choice_id and classification and patient_finding_instance:
                location_choice = classification.choices.filter(id=location_choice_id).first()

                if location_choice:
                    patient_finding_location_serializer = PatientFindingLocationSerializer(data={
                        "location_classification": classification.id,
                        "location_choice": location_choice.id,
                        "subcategories": location_choice.subcategories,
                        "numerical_descriptors": location_choice.numerical_descriptors
                    })

                    if patient_finding_location_serializer.is_valid():
                        patient_finding_location_instance = patient_finding_location_serializer.save()
                        patient_finding_instance.locations.add(patient_finding_location_instance)
                        patient_finding_instance.save()
                        print(" Linked PatientFindingLocation to PatientFinding")
                    else:
                        return Response(patient_finding_location_serializer.errors, status=status.HTTP_400_BAD_REQUEST)

            # Save Morphology Data
            patient_finding_morphology_instance = None
            if morphology_classification_id and morphology_choice_id:
                patient_finding_morphology_serializer = PatientFindingMorphologySerializer(data={
                    "morphology_classification_id": morphology_classification_id,
                    "morphology_choice_id": morphology_choice_id
                })

                if patient_finding_morphology_serializer.is_valid():
                    patient_finding_morphology_instance = patient_finding_morphology_serializer.save()
                    print(" Saved PatientFindingMorphology:", patient_finding_morphology_instance)
                else:
                    return Response(patient_finding_morphology_serializer.errors, status=status.HTTP_400_BAD_REQUEST)

            # Return Final Response
            return Response(
                {
                    "message": "Data saved successfully",
                    "patient_examination_id": patient_examination.id,
                    "finding_id": finding_id,
                    "patient_finding_id": patient_finding_instance.id if patient_finding_instance else None,
                    "finding_location_classification_id": classification_id,
                    "finding_location_choices": classification_choices,
                    "selected_location_choice_id": location_choice_id if patient_finding_location_instance else None,
                    "patient_finding_location_id": patient_finding_location_instance.id if patient_finding_location_instance else None,
                    "morphology_classification_id": morphology_classification_id if patient_finding_morphology_instance else None,
                    "morphology_choice_id": morphology_choice_id if patient_finding_morphology_instance else None
                },
                status=status.HTTP_201_CREATED
            )

        return Response(patient_examination_serializer.errors, status=status.HTTP_400_BAD_REQUEST)
'''
