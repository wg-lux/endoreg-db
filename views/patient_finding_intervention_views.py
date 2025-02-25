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
    PatientExaminationSerializer, PatientFindingSerializer, PatientFindingMorphologySerializer,PatientFindingMorphologyRelationSerializer,PatientFindingInterventionSerializer
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

from rest_framework.decorators import api_view
from rest_framework.response import Response
from rest_framework import status
from endoreg_db.models import FindingMorphologyClassification, FindingMorphologyClassificationChoice
from endoreg_db.serializers.patient_finding_interventions import FindingMorphologyClassificationChoiceSerializer

@api_view(['GET'])
def get_morphology_choices(request, classification_id):  
    """
    Fetches FindingMorphologyClassificationChoice records based on the selected classification ID.
    """
    print("here i am ------ ----- ----- ------- ----- -------",classification_id)
    try:
        # Debugging: Check if classification_id is received correctly
        print(f"Received classification_id: {classification_id}")

        # Ensure classification exists
        classification = FindingMorphologyClassification.objects.filter(id=classification_id).first()
        print(classification)
        
        if not classification:
            return Response({"error": "Morphology classification not found"}, status=status.HTTP_404_NOT_FOUND)
        
        # Fetch all choices for the given classification ID
        choices = FindingMorphologyClassificationChoice.objects.filter(classification_id=classification_id)

        # Ensure choices exist
        if not choices.exists():
            return Response({"error": "No morphology choices found for this classification"}, status=status.HTTP_404_NOT_FOUND)

        # Serialize and return the choices
        serializer = FindingMorphologyClassificationChoiceSerializer(choices, many=True)
        
        return Response(serializer.data, status=status.HTTP_200_OK)
    
    except Exception as e:
        print(f"-------------Error encountered----------: {e}")  # Debugging statement
        return Response({"error": f"Server error: {str(e)}"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)




@api_view(['GET'])
def get_all_interventions(request):
    """
    API to fetch all FindingIntervention records for the frontend dropdown.
    """
    interventions = FindingIntervention.objects.all()
    serializer = FindingInterventionSerializer(interventions, many=True)
    return Response(serializer.data, status=status.HTTP_200_OK)



@api_view(['POST'])
def final_submit(request):
    """
    API to save:
     - `PatientExamination`
     - `PatientFinding` (if `finding_id` exists)
     - `FindingLocationClassification` (colonoscopy_default)
     - `PatientFindingLocation` (if `location_choice_id` is provided)
     - `PatientFindingMorphology` (if `morphology_classification_id` & `morphology_choice_id` are provided)
     - `PatientFinding_Morphology` relationship (links `PatientFinding` to `PatientFindingMorphology`)
     - `PatientFindingIntervention` (if `intervention_id` is provided)
     - Ensures all data validation before saving.
    """

    with transaction.atomic():
        # Extract additional fields from request
        location_choice_id = request.data.pop("location_choice_id", None)
        morphology_classification_id = request.data.pop("morphology_classification_id", None)
        morphology_choice_id = request.data.pop("morphology_choice_id", None)
        intervention_id = request.data.pop("intervention_id", None)  # New: Extract intervention selection

        # Save PatientExamination using Serializer
        patient_examination_serializer = PatientExaminationSerializer(data=request.data)

        if patient_examination_serializer.is_valid():
            patient_examination = patient_examination_serializer.save()
            print("Saved PatientExamination:", patient_examination)

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
                        print("Saved PatientFinding:", patient_finding_instance)
                    else:
                        return Response(patient_finding_serializer.errors, status=status.HTTP_400_BAD_REQUEST)

            # Fetch FindingLocationClassification (Default: colonoscopy_default)
            classification = FindingLocationClassification.objects.filter(name="colonoscopy_default").first()
            classification_id = classification.id if classification else None

            # Handle `PatientFindingLocation`
            patient_finding_location_instance = None
            if location_choice_id and classification and patient_finding_instance:
                location_choice = classification.choices.filter(id=location_choice_id).first()

                if location_choice:
                    patient_finding_location_serializer = PatientFindingLocationSerializer(data={
                        "location_classification": classification.id,
                        "location_choice": location_choice.id,
                    })

                    if patient_finding_location_serializer.is_valid():
                        patient_finding_location_instance = patient_finding_location_serializer.save()
                        patient_finding_instance.locations.add(patient_finding_location_instance)
                        patient_finding_instance.save()
                        print("Linked PatientFindingLocation to PatientFinding")
                    else:
                        return Response(patient_finding_location_serializer.errors, status=status.HTTP_400_BAD_REQUEST)

            # Handle `PatientFindingMorphology`
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
            relation_instance = None
            if patient_finding_instance and patient_finding_morphology_instance:
                pivot_model = PatientFinding.morphologies.through  
                relation_instance = pivot_model.objects.create(
                    patientfinding_id=patient_finding_instance.id,  
                    patientfindingmorphology_id=patient_finding_morphology_instance.id  
                )
                print("Linked PatientFindingMorphology to PatientFinding")

            # Handle `PatientFindingIntervention`
            patient_finding_intervention_instance = None
            if intervention_id and patient_finding_instance:
                intervention = FindingIntervention.objects.filter(id=intervention_id).first()

                if intervention:
                    patient_finding_intervention_serializer = PatientFindingInterventionSerializer(data={
                        "patient_finding_id": patient_finding_instance.id,
                        "intervention_id": intervention.id
                    })

                    if patient_finding_intervention_serializer.is_valid():
                        patient_finding_intervention_instance = patient_finding_intervention_serializer.save()
                        print("Saved PatientFindingIntervention:", patient_finding_intervention_instance)
                    else:
                        return Response(patient_finding_intervention_serializer.errors, status=status.HTTP_400_BAD_REQUEST)

            # Return Final Response
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
                    "patientfindingmorphology_relation_id": relation_instance.id if relation_instance else None,
                    "intervention_id": intervention_id if patient_finding_intervention_instance else None
                },
                status=status.HTTP_201_CREATED
            )

        return Response(patient_examination_serializer.errors, status=status.HTTP_400_BAD_REQUEST)




#DOESN'T WORK PROPERLY YET:This query i sto just test, by fetching  the patient examination finding classification morphology and intervention details saved in the data tables
'''
it can be run using:

var patient_id = 7;  // Change this to the required patient ID

fetch(`http://127.0.0.1:8000/endoreg_db/api/patient-details/${patient_id}/`)  
    .then(response => response.json())
    .then(data => console.log(data))
    .catch(error => console.error('Error:', error));


'''

import os
from django.db import connection
from django.conf import settings  
from rest_framework.decorators import api_view
from endoreg_db.serializers.patient_finding_interventions import PatientDetailsSerializer

@api_view(['GET'])
def get_patient_details(request, patient_id):
    """
    API to fetch all details for a given patient ID, return JSON, and save to a formatted text file.
    """

    query = """
    WITH patient_data AS (
        SELECT
            p.id AS patient_id,
            CONCAT(p.first_name, ' ', p.last_name) AS patient_name,  
            p.dob AS patient_dob
        FROM endoreg_db_patient p
        WHERE p.id = %s
    ),

    patient_examination_data AS (
        SELECT
            pe.patient_id,
            pe.examination_id,
            e.name AS examination_name,
            pe.id AS patient_examination_id,
            pe.date_start,
            pe.date_end
        FROM endoreg_db_patientexamination pe
        JOIN endoreg_db_examination e ON pe.examination_id = e.id
        WHERE pe.patient_id = %s
    ),

    patient_finding_data AS (
        SELECT
            pf.id AS patient_finding_id,
            pf.finding_id,
            f.name AS finding_name,
            pf.patient_examination_id
        FROM endoreg_db_patientfinding pf
        JOIN endoreg_db_finding f ON pf.finding_id = f.id
        WHERE pf.patient_examination_id IN (SELECT patient_examination_id FROM patient_examination_data)
    ),

    patient_finding_location_data AS (
        SELECT
            pfl.patientfinding_id,
            flc.id AS location_classification_id,
            flcc.id AS location_classification_choice_id,
            flc.name AS location_classification_name,
            flcc.name AS location_choice_name
        FROM endoreg_db_patientfinding_locations pfl
        JOIN endoreg_db_patientfindinglocation pfl_loc 
            ON pfl.patientfindinglocation_id = pfl_loc.id
        JOIN endoreg_db_findinglocationclassification flc 
            ON pfl_loc.location_classification_id = flc.id
        JOIN endoreg_db_findinglocationclassificationchoice flcc 
            ON pfl_loc.location_choice_id = flcc.id
        WHERE pfl.patientfinding_id IN (SELECT patient_finding_id FROM patient_finding_data)
    ),

    patient_finding_morphology_data AS (
        SELECT
            pfm.morphology_choice_id,
            pfm.morphology_classification_id,
            pfm_rel.patientfinding_id,
            fmc.name AS morphology_classification_name,
            fmcc.name AS morphology_choice_name
        FROM endoreg_db_patientfindingmorphology pfm
        JOIN endoreg_db_findingmorphologyclassification fmc 
            ON pfm.morphology_classification_id = fmc.id
        JOIN endoreg_db_findingmorphologyclassificationchoice fmcc 
            ON pfm.morphology_choice_id = fmcc.id
        JOIN endoreg_db_patientfinding_morphologies pfm_rel
            ON pfm.id = pfm_rel.patientfindingmorphology_id
        WHERE pfm_rel.patientfinding_id IN (SELECT patient_finding_id FROM patient_finding_data)
    ),

    patient_finding_intervention_data AS (
        SELECT
            pfi.patient_finding_id,
            pfi.intervention_id,
            fi.name AS intervention_name
        FROM endoreg_db_patientfindingintervention pfi
        JOIN endoreg_db_findingintervention fi ON pfi.intervention_id = fi.id
        WHERE pfi.patient_finding_id IN (SELECT patient_finding_id FROM patient_finding_data)
    )

    SELECT
        pd.patient_name,
        pd.patient_dob,
        pe.date_start,
        pe.date_end,
        pe.examination_name,
        pf.finding_name,
        pfl.location_classification_id,
        pfl.location_classification_name,
        pfl.location_choice_name,
        pfm.morphology_classification_name,
        pfm.morphology_choice_name,
        pfi.intervention_name
    FROM patient_data pd
    LEFT JOIN patient_examination_data pe ON pe.patient_id = pd.patient_id
    LEFT JOIN patient_finding_data pf ON pf.patient_examination_id = pe.patient_examination_id
    LEFT JOIN patient_finding_location_data pfl ON pfl.patientfinding_id = pf.patient_finding_id
    LEFT JOIN patient_finding_morphology_data pfm ON pfm.patientfinding_id = pf.patient_finding_id
    LEFT JOIN patient_finding_intervention_data pfi ON pfi.patient_finding_id = pf.patient_finding_id;
    """

    with connection.cursor() as cursor:
        cursor.execute(query, [patient_id, patient_id])
        columns = [col[0] for col in cursor.description]
        results = [dict(zip(columns, row)) for row in cursor.fetchall()]

    if not results:
        return Response({"message": "No data found for the given patient ID"}, status=404)

    # Serialize the data
    serializer = PatientDetailsSerializer(results, many=True)

    # Formatting the output for saving
    formatted_results = []
    for row in serializer.data:
        formatted_text = f"""
        {row['patient_name']} - {row['patient_dob']} - Patient Examination ({row['date_start']} - {row['date_end']})
        {row['examination_name']} - None - {row['finding_name']}
        Finding Location: [<PatientFindingLocation: {row['location_classification_name']} - {row['location_choice_name']}>]
        Finding Morphology: [<PatientFindingMorphology: {row['morphology_classification_name']} - {row['morphology_choice_name']} ({row['morphology_classification_name']})>]
        {row['intervention_name']}
        """
        formatted_results.append(formatted_text.strip())

    # Save formatted output to a file
    save_dir = os.path.join(settings.BASE_DIR, "patient_intervention_reports_from_database")
    os.makedirs(save_dir, exist_ok=True)  # Create directory if it doesn't exist
    file_path = os.path.join(save_dir, f"patient_{patient_id}_report.txt")

    with open(file_path, "w", encoding="utf-8") as file:
        file.write("\n\n".join(formatted_results))

    return Response({
        "message": "Data saved successfully",
        "file_path": file_path,
        "formatted_details": serializer.data
    }, status=status.HTTP_200_OK)



