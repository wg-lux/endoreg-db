from endoreg_db.models import Unit

def validate_subcategory_dict(self, subcategory_dict:dict=None):
    if subcategory_dict is None:
        return False
    
    if not isinstance(subcategory_dict, dict):
        return False

    # check if key choices exists and is a list of strings
    if "choices" not in subcategory_dict:
        return False
    
    if not isinstance(subcategory_dict["choices"], list):
        return False
    
    for choice in subcategory_dict["choices"]:
        if not isinstance(choice, str):
            return False
        
    # check if key default exists and is a string
    if "default" not in subcategory_dict:
        return False
    
    if not isinstance(subcategory_dict["default"], str):
        return False
    
    # check if default is in choices
    if subcategory_dict["default"] not in subcategory_dict["choices"]:
        return False
    
    if not subcategory_dict["required"]:
        return False
    
    return True


def validate_numerical_descriptor(self, numerical_descriptor_dict:dict=None):
    # Check if numerical_descriptor_dict is None
    error_message = ""
    if numerical_descriptor_dict is None:
        error_message = "numerical_descriptor_dict is None"
        return False, error_message
    
    # Check if numerical_descriptor_dict is a dictionary
    if not isinstance(numerical_descriptor_dict, dict):
        error_message = "numerical_descriptor_dict is not a dictionary"
        return False, error_message
    
    # if key unit exists and is a string and Unit object with that name exists
    if "unit" not in numerical_descriptor_dict:
        error_message = "unit key does not exist in numerical_descriptor_dict"
        return False, error_message
    
    elif not isinstance(numerical_descriptor_dict["unit"], str):
        error_message = "unit key is not a string"
        return False, error_message

    elif not Unit.objects.filter(name=numerical_descriptor_dict["unit"]).exists():
        error_message = "Unit object with that name does not exist"
        return False, error_message
    
    
    if not "required" in numerical_descriptor_dict:
        error_message = "required key does not exist in numerical_descriptor_dict"
        return False, error_message
    
    elif not isinstance(numerical_descriptor_dict["required"], bool):
        error_message = "required key is not a boolean"
        return False, error_message
    
    # check if min, max, mean, std exist and are either None or float
    for key in ["min", "max", "mean", "std", "default"]:
        if key not in numerical_descriptor_dict:
            error_message = f"{key} key does not exist in numerical_descriptor_dict"
            return False, error_message
        
        if numerical_descriptor_dict[key] is not None and not isinstance(numerical_descriptor_dict[key], float):
            error_message = f"{key} key is not a float"
            return False, error_message
        
    # check if distribution exists and is either "normal" or "uniform"
    if "distribution" not in numerical_descriptor_dict:
        error_message = "distribution key does not exist in numerical_descriptor_dict"
        return False, error_message
    
    if numerical_descriptor_dict["distribution"] not in ["normal", "uniform"]:
        error_message = "distribution key is not either 'normal' or 'uniform'"
        return False, error_message
    
    return True, None