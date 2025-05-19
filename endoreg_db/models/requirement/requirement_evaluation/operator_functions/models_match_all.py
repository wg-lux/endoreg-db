from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from endoreg_db.models import Requirement

def _models_match_all(requirement: "Requirement", **kwargs):
    """
    Match all models for a given requirement.
    
    Retrieves the models dictionary from the requirement by calling its get_models_dict() method.
    Additional keyword arguments are accepted for future extensions. Note that this function
    currently only extracts the models without performing any matching logic.
    """
    models_dict = requirement.get_models_dict()

    #TODO
    assert 1==0