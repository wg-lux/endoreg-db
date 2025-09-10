from endoreg_db.models.requirement.requirement import Requirement
from endoreg_db.models.requirement.requirement_set import RequirementSet

from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response

@api_view(['POST'])
def evaluate_requirements(request):
    """
    Evaluate requirements based on the logic expressed in RequirementLinks data.

    Expects a JSON payload with the following structure:
    {
        "requirement_set_ids": [<requirement_set_ids>],
        "patient_examination_id": <patient_examination_id>
    }

    If requirement_set_ids is not provided, all RequirementSets will be evaluated.

    Returns a JSON response with the evaluation results:
    {
        "results": [
            {
                "requirement_name": <requirement_name>,
                "met": <true/false>,
                "details": <additional_details>
            },
            ...
        ]
    }
    """
    try:
        data = request.data
        requirement_set_ids = data.get("requirement_set_ids", None)
        patient_examination_id = data.get("patient_examination_id")

        if not patient_examination_id:
            return Response({"error": "patient_examination_id is required"}, status=status.HTTP_400_BAD_REQUEST)

        # Get the patient examination
        from endoreg_db.models.medical.patient.patient_examination import PatientExamination
        try:
            patient_examination = PatientExamination.objects.get(id=patient_examination_id)
        except PatientExamination.DoesNotExist:
            return Response({"error": f"PatientExamination with id {patient_examination_id} does not exist"}, status=status.HTTP_404_NOT_FOUND)

        # Determine which RequirementSets to evaluate
        if requirement_set_ids:
            requirement_sets = RequirementSet.objects.filter(id__in=requirement_set_ids)
        else:
            requirement_sets = RequirementSet.objects.all()

        results = []

        for req_set in requirement_sets:
            for requirement in req_set.requirements.all():
                requirement_obj = Requirement.objects.get(name=requirement.name)  # Ensure fresh instance
                try:
                    met, details = requirement_obj.evaluate_with_details(patient_examination.patient, mode="strict")
                    feedback = details if isinstance(details, str) else "Voraussetzung erfüllt" if met else "Voraussetzung nicht erfüllt"
                except (TypeError, ValueError) as e:
                    met = False
                    feedback = f"Fehler bei der Bewertung der Voraussetzung: {str(e)}"
                results.append({
                    "requirement_name": requirement_obj.name,
                    "met": met,
                    "details": feedback
                })

        return Response({"results": results}, status=status.HTTP_200_OK)

    except Exception as e:
        return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)
    

def evaluate_requirement_set(request) -> Response:
    """
    Evaluate a specific RequirementSet based on provided RequirementLinks data.

    Expects a JSON payload with the following structure:
    {
        "requirement_set_ids": [<requirement_set_ids>],
        "patient_examination_id": <patient_examination_id>
    }

    Returns a JSON response with the evaluation results for the specified RequirementSet:
    {
        "results": [
            {
                "requirement_name": <requirement_name>,
                "met": <true/false>,
                "details": <additional_details>
            },
            ...
        ]
    }
    """
    try:
        data = request.data
        requirement_set_ids = data.get("requirement_set_ids", None)
        patient_examination_id = data.get("patient_examination_id")

        if not requirement_set_ids:
            return Response({"error": "requirement_set_ids is required"}, status=status.HTTP_400_BAD_REQUEST)
        
        if not patient_examination_id:
            return Response({"error": "patient_examination_id is required"}, status=status.HTTP_400_BAD_REQUEST)

        # Get the patient examination
        from endoreg_db.models.medical.patient.patient_examination import PatientExamination
        try:
            patient_examination = PatientExamination.objects.get(id=patient_examination_id)
        except PatientExamination.DoesNotExist:
            return Response({"error": f"PatientExamination with id {patient_examination_id} does not exist"}, status=status.HTTP_404_NOT_FOUND)

        results = []

        # Construct RequirementLinks from the provided data
        for requirement_set_id in requirement_set_ids:
            # Fetch the specified RequirementSet
            try:
                req_set = RequirementSet.objects.get(id=requirement_set_id)
            except RequirementSet.DoesNotExist:
                return Response({"error": f"RequirementSet with id {requirement_set_id} does not exist"}, status=status.HTTP_404_NOT_FOUND)

            for requirement in req_set.requirements.all():
                met, details = requirement.evaluate(patient_examination.patient, mode="strict")
                results.append({
                    "requirement_name": requirement.name,
                    "met": met,
                    "details": details
                })

        return Response({
            "results": results
        }, status=status.HTTP_200_OK)

    except Exception as e:
        return Response({"results": str(e), details: "Fehler bei der Bewertung der Voraussetzung"}, status=status.HTTP_400_BAD_REQUEST)