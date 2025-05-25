from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from endoreg_db.utils.links.requirement_link import RequirementLinks
    from endoreg_db.models.requirement.requirement import Requirement


def _evaluate_models_match_any(
    requirement_links: "RequirementLinks",
    input_links: "RequirementLinks",
    **kwargs
) -> bool:
    """
    Checks if the requirement_links matches any of the input_links.
    
    Args:
        requirement_links: The reference set of requirement links to compare against.
        input_links: The aggregated requirement links from the input objects.
    
    Returns:
        True if the input set of requirement links matches according to requirement_links.match_any; otherwise, False.
    """
    return requirement_links.match_any(input_links)

def _evaluate_models_match_all(
    requirement_links: "RequirementLinks",
    input_links: "RequirementLinks",
    **kwargs
) -> bool:
    """
    Evaluates if all active links in requirement_links are present in input_links.

    For each category of links in requirement_links (e.g., diseases, examinations),
    all items specified in that category in requirement_links must be present in the
    corresponding category in input_links.

    Args:
        requirement_links: The RequirementLinks object from the Requirement model.
        input_links: The aggregated RequirementLinks object from the input arguments.
        **kwargs: Additional keyword arguments (currently unused).

    Returns:
        True if all specified items in requirement_links are found in input_links,
        False otherwise.
    """
    active_req_links = requirement_links.active() # Get dict of non-empty lists from requirement

    if not active_req_links: # If the requirement specifies no actual items to link
        return True # Vacuously true, as there are no conditions to fail

    for link_category_name, req_items_list in active_req_links.items():
        input_items_list = getattr(input_links, link_category_name, [])
        
        try:
            set_input_items = set(input_items_list)
            set_req_items = set(req_items_list)
        except TypeError: 
            for req_item in req_items_list:
                if req_item not in input_items_list:
                    return False 
            continue 

        if not set_req_items.issubset(set_input_items):
            return False 
            
    return True

def dispatch_operator_evaluation(
    operator_name: str,
    requirement_links: "RequirementLinks",
    input_links: "RequirementLinks",
    **kwargs
) -> bool:
    """
    Dispatches the evaluation to the appropriate function based on the operator name.
    
    Args:
        operator_name: The name of the operator to evaluate.
        requirement_links: The RequirementLinks object from the Requirement model.
        input_links: The aggregated RequirementLinks object from the input arguments.
        **kwargs: Additional keyword arguments for specific operator logic.
                    For lab value operators, this includes 'requirement' (the Requirement model instance).

    Returns:
        True if the condition defined by the operator is met, False otherwise.
        
    Raises:
        NotImplementedError: If the evaluation logic for the operator's name is not implemented.
    """
    from .lab_value_operators import LAB_VALUE_OPERATOR_FUNCTIONS
    from endoreg_db.models.requirement.requirement import Requirement # Runtime import for isinstance

    eval_func = None

    if operator_name == "models_match_any":
        eval_func = _evaluate_models_match_any
        return eval_func(
            requirement_links=requirement_links,
            input_links=input_links,
            **kwargs
        )
    elif operator_name == "models_match_all":
        eval_func = _evaluate_models_match_all
        return eval_func(
            requirement_links=requirement_links,
            input_links=input_links,
            **kwargs
        )
    elif operator_name in LAB_VALUE_OPERATOR_FUNCTIONS:
        requirement = kwargs.get("requirement")
        assert requirement is not None, "Lab value operators require 'requirement' in kwargs."
        assert isinstance(requirement, Requirement), "Lab value operators require 'requirement' of type Requirement in kwargs."
        
        eval_func = LAB_VALUE_OPERATOR_FUNCTIONS[operator_name]
        return eval_func(
            input_links=input_links,
            requirement=requirement, 
            operator_kwargs=kwargs
        )
    else:
        raise NotImplementedError(f"Evaluation logic for operator '{operator_name}' is not implemented.")
