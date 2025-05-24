from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from endoreg_db.models import Requirement, RequirementType

def model_by_name(model_name: str):
    """
    Returns the model class associated with the specified model name string.
    
    Args:
        model_name: The string identifier for the desired model class. Accepts both singular and plural forms.
    
    Returns:
        The model class corresponding to the provided model name.
    
    Raises:
        ValueError: If the model name is not recognized.
    """

    from endoreg_db.models import (
        RequirementType,
        RequirementOperator,
        RequirementSet,
        Examination,
        ExaminationIndication,
        LabValue,
        Disease,
        DiseaseClassificationChoice,
        Event,
        Finding,
        FindingMorphologyClassificationChoice,
        FindingLocationClassificationChoice,
        FindingIntervention,
    )

    lookup = {
        "requirement_type": RequirementType,
        "requirement_types": RequirementType,
        "requirement_operator": RequirementOperator,
        "requirement_operators": RequirementOperator,
        "requirement_set": RequirementSet,
        "requirement_sets": RequirementSet,
        "examination": Examination,
        "examinations": Examination,
        "examination_indication": ExaminationIndication,
        "examination_indications": ExaminationIndication,
        "lab_value": LabValue,
        "lab_values": LabValue,
        "disease": Disease,
        "diseases": Disease,
        "disease_classification_choice": DiseaseClassificationChoice,
        "disease_classification_choices": DiseaseClassificationChoice,
        "event": Event,
        "events": Event,
        "finding": Finding,
        "findings": Finding,
        "finding_morphology_classification_choice": FindingMorphologyClassificationChoice,
        "finding_morphology_classification_choices": FindingMorphologyClassificationChoice,
        "finding_location_classification_choice": FindingLocationClassificationChoice,
        "finding_location_classification_choices": FindingLocationClassificationChoice,
        "finding_intervention": FindingIntervention,
        "finding_interventions": FindingIntervention,
        # Add more mappings as needed
    }

    if model_name not in lookup:
        raise ValueError(f"Unknown model name: {model_name}. Expected one of {list(lookup.keys())}")
    
    return lookup[model_name]

def get_requirement_links_model_lookup():
    """
    Returns a dictionary mapping requirement type names to their corresponding model classes.
    
    This mapping enables dynamic retrieval of model classes based on requirement type names,
    facilitating evaluation and linking logic for requirements.
    """
    from endoreg_db.models import (
        RequirementType,
        RequirementOperator,
        RequirementSet,
        Examination,
        ExaminationIndication,
        LabValue,
        Disease,
        DiseaseClassificationChoice,
        Event,
        Finding,
        FindingMorphologyClassificationChoice,
        FindingLocationClassificationChoice,
        FindingIntervention,
    )

    return {
        # "requirement_types": RequirementType,
        # "operators": RequirementOperator,
        # "requirement_sets": RequirementSet,
        "examinations": Examination,
        "examination_indications": ExaminationIndication,
        "lab_values": LabValue,
        "diseases": Disease,
        "disease_classification_choices": DiseaseClassificationChoice,
        "events": Event,
        "findings": Finding,
        "finding_morphology_classification_choices": FindingMorphologyClassificationChoice,
        "finding_location_classification_choices": FindingLocationClassificationChoice,
        "finding_interventions": FindingIntervention,
        # Add more mappings as needed
    }


def _match_all_links(
        requirement: "Requirement", 
        r_type: "RequirementType",
        **kwargs
    ):
    """
        Validates and prepares all model links for a given requirement and requirement type.
        
        Checks that the provided source object matches the expected model class for the requirement type, and verifies that all active link targets correspond to known model classes. Raises a ValueError if any target model is unknown or if the required source object is missing. Actual matching logic is not yet implemented.
        """
    links = requirement.links
    active_links = links.active()

    source_model = model_by_name(r_type.name)
    source = kwargs.get(r_type.name, None)
    assert isinstance(source, source_model), f"Expected {source_model}, got {type(source)}"


    target_model_lookup = get_requirement_links_model_lookup()
    target_models = [target_model_lookup.get(link.name, None) for link in active_links.keys()]
    
    for target_model in target_models:
        if target_model is None:
            raise ValueError(f"Unknown model name: {target_model}. Expected one of {list(target_model_lookup.keys())}")
        
    
    

    source = kwargs.get(r_type.name, None)
    if source is None:
        raise ValueError(f"No data found for requirement type: {r_type.name}, Expected {r_type.name} in kwargs")
    


    #TODO
    assert 1==0

def _match_any_links(requirement: "Requirement", **kwargs):
    """
    Stub for matching any linked models for a given requirement.
    
    Currently not implemented; raises an assertion error if called.
    """
    links = requirement.links

    #TODO
    assert 1==0

def _match_no_links(requirement: "Requirement", **kwargs):
    """
    Stub for matching no models for a given requirement.
    
    This function is a placeholder and does not implement any matching logic.
    """
    links = requirement.links

    #TODO
    assert 1==0