from typing import Any
from endoreg_db.models.requirement.requirement_set import RequirementSet
from endoreg_db.models.medical.patient.patient_examination import PatientExamination
from endoreg_db.services.lookup_store import LookupStore


def get_evaluation_input_for_set(
    requirement_set: RequirementSet,
    patient_examination: PatientExamination,
    store: LookupStore
) -> Any:
    """
    Helper function to safely access the protected _get_evaluation_input method.

    This function acts as a public interface to retrieve the appropriate
    evaluation input for a given requirement set, avoiding direct access
    to protected members from views.

    Args:
        requirement_set: The RequirementSet instance.
        patient_examination: The context object for the evaluation.
        store: The lookup store instance.

    Returns:
        The appropriate evaluation input for the requirement set.
    """
    # This helper safely calls the protected method.
    return requirement_set._get_evaluation_input(patient_examination, store)
