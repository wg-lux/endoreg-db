from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from endoreg_db.models import Requirement, RequirementType

def model_by_name(model_name: str):
    """
    Returns the ORM model class corresponding to the specified model name.
    
    Args:
        model_name: The string name of the model, accepting both singular and plural forms.
    
    Returns:
        The ORM model class associated with the given model name.
    
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
    Returns a mapping of requirement type names to their corresponding ORM model classes.
    
    This mapping enables dynamic resolution of model classes based on requirement type names during requirement link evaluation.
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
        Validates and prepares model classes for all active links of a requirement.
        
        Checks that the provided source object matches the expected model class for the requirement type, and resolves target model classes for each active link. Raises an error if any required model is missing or unknown.
        
        Args:
            requirement: The requirement instance whose links are to be matched.
            r_type: The type of the requirement, used to determine the source model class.
            **kwargs: Must include an instance of the source model keyed by the requirement type name.
        
        Raises:
            ValueError: If a required model is missing or unknown, or if the source object is not provided.
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
    Attempts to match any linked models for the given requirement.
    
    Currently a stub; does not implement matching logic.
    """
    links = requirement.links

    #TODO
    assert 1==0

def _match_no_links(requirement: "Requirement", **kwargs):
    """
    Returns no matches for models linked to the given requirement.
    
    This function is a placeholder and does not implement any matching logic.
    """
    links = requirement.links

    #TODO
    assert 1==0