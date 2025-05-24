
from .models_match_all import _match_all_links


SUPPORTED_OPERATORS = {
    "models_match_all": _match_all_links,
}

def get_operator_function(operator_name:str):
    """
    Retrieves the operator function for the given operator name.
    
    This function looks up the operator name in the supported operators mapping
    and returns the corresponding operator function used for evaluating requirements.
    If the operator name is not recognized, the behavior is undefined.
     
    Args:
        operator_name: The name of the operator to retrieve.
    
    Returns:
        The operator function associated with the operator name.
    """
    
    operator_function = SUPPORTED_OPERATORS.get(operator_name)
    if operator_function is None:
        raise ValueError(f"Operator '{operator_name}' is not supported.")
    return operator_function




