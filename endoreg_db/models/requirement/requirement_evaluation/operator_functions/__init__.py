from .models_match_all import _match_all_links


SUPPORTED_OPERATORS = {
    "models_match_all": _match_all_links,
}

def get_operator_function(operator_name:str):
    """
    Returns the operator function associated with the specified operator name.
    
    Raises:
        ValueError: If the operator name is not supported.
    """
    
    operator_function = SUPPORTED_OPERATORS.get(operator_name)
    if operator_function is None:
        raise ValueError(f"Operator '{operator_name}' is not supported.")
    return operator_function




