import datetime # Add import
from datetime import timedelta # Add import
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from endoreg_db.utils.links.requirement_link import RequirementLinks
    from endoreg_db.models.requirement.requirement import Requirement
    # from endoreg_db.models import Unit # Potentially needed for dynamic unit handling


# Helper function to check if a date is within the timeframe specified by a Requirement
def _is_date_in_timeframe(date_to_check: datetime.date | None, requirement: "Requirement") -> bool:
    if date_to_check is None:
        return False
    if not requirement.unit or requirement.numeric_value_min is None or requirement.numeric_value_max is None:
        return False # Not enough information for timeframe evaluation

    # For now, primarily supporting 'days'. Extend if other units are common.
    if requirement.unit.name.lower() != "days":
        # Log a warning or raise NotImplementedError if unsupported unit is critical
        # logger.warning(f"Timeframe unit '{requirement.unit.name}' not fully supported for generic timeframe check, assuming days or failing.")
        return False # Or handle other units specifically

    today = datetime.date.today()
    # numeric_value_min is typically negative for "days ago" (e.g., -30 for 30 days ago)
    # numeric_value_max is typically 0 for "today"
    timeframe_start_delta = int(requirement.numeric_value_min)
    timeframe_end_delta = int(requirement.numeric_value_max)

    start_date_bound = today + timedelta(days=timeframe_start_delta)
    end_date_bound = today + timedelta(days=timeframe_end_delta)

    return start_date_bound <= date_to_check <= end_date_bound


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


def _evaluate_models_match_any_in_timeframe(
    requirement_links: "RequirementLinks",
    input_links: "RequirementLinks",
    requirement: "Requirement", # Explicitly pass Requirement
    **kwargs # Keep for consistency, though 'requirement' is the main one used here
) -> bool:
    """
    Checks if any relevant model in input_links matches a model specified in
    requirement_links AND falls within the timeframe defined on the Requirement.

    Currently focuses on PatientEvent instances and their dates.
    """
    active_req_links_dict = requirement_links.active()
    if not active_req_links_dict:
        # If the Requirement itself doesn't specify any models to match (e.g., requirement.events is empty),
        # then it's vacuously true that "any" of these (non-existent) required models are matched.
        # The timeframe aspect becomes irrelevant if no specific models are being checked.
        return True

    # --- Handle PatientEvents ---
    # Check if the requirement is concerned with events
    if requirement_links.events: # This list contains Event model instances
        required_event_models = set(requirement_links.events) # Target Event models from the Requirement
        
        # input_links.patient_events contains PatientEvent instances provided as input
        for patient_event_instance in input_links.patient_events:
            # Check if the event of the current PatientEvent instance is one of the target events
            if patient_event_instance.event in required_event_models:
                # If it is, check if this PatientEvent's date is within the timeframe
                if _is_date_in_timeframe(patient_event_instance.date, requirement):
                    return True # Found a matching event within the timeframe

    # --- Handle Other Model Types (Example: PatientLabValue) ---
    # if requirement_links.lab_values:
    #     required_lab_value_models = set(requirement_links.lab_values)
    #     for plv_instance in input_links.patient_lab_values:
    #         if plv_instance.lab_value in required_lab_value_models:
    #             date_to_check = None
    #             if hasattr(plv_instance, 'date_time_value') and plv_instance.date_time_value:
    #                 date_to_check = plv_instance.date_time_value.date()
    #             elif hasattr(plv_instance, 'date') and plv_instance.date: # If it had a simple date field
    #                 date_to_check = plv_instance.date
    #
    #             if _is_date_in_timeframe(date_to_check, requirement):
    #                 return True
    
    # If the code reaches here, no matching model within the timeframe was found
    # for any of the categories specified in requirement_links.
    return False


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
    requirement = kwargs.get("requirement") # Get requirement for operators that need it

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
    elif operator_name == "models_match_any_in_timeframe":
        # 'requirement' is already extracted from kwargs via requirement = kwargs.get("requirement")
        if not isinstance(requirement, Requirement): # Ensure requirement is present and correct type
            raise ValueError("models_match_any_in_timeframe operator requires a valid 'requirement' instance in kwargs.")
        
        # Create a new kwargs dict for the call, excluding 'requirement' to avoid passing it twice,
        # as it's already an explicit parameter for _evaluate_models_match_any_in_timeframe.
        kwargs_for_eval = {k: v for k, v in kwargs.items() if k != 'requirement'}
        
        eval_func = _evaluate_models_match_any_in_timeframe
        return eval_func(
            requirement_links=requirement_links,
            input_links=input_links,
            requirement=requirement, # Pass the requirement instance explicitly
            **kwargs_for_eval      # Pass the remaining kwargs
        )
    elif operator_name in LAB_VALUE_OPERATOR_FUNCTIONS:
        if not isinstance(requirement, Requirement): # Ensure requirement is present and correct type
            raise ValueError(f"Lab value operator \'{operator_name}\' requires a valid 'requirement' instance in kwargs.")
        
        eval_func = LAB_VALUE_OPERATOR_FUNCTIONS[operator_name]
        return eval_func(
            input_links=input_links,
            requirement=requirement, 
            operator_kwargs=kwargs
        )
    else:
        raise NotImplementedError(f"Evaluation logic for operator '{operator_name}' is not implemented.")
