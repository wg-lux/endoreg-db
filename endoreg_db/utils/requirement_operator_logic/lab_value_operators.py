from typing import TYPE_CHECKING, List, Optional, Callable, Dict, Any
import re # Added import
from endoreg_db.models.medical.patient.patient_lab_value import PatientLabValue
from endoreg_db.models.requirement.requirement import Requirement
from datetime import datetime, timedelta

if TYPE_CHECKING:
    from endoreg_db.utils.links.requirement_link import RequirementLinks # Added import
    # from endoreg_db.models.requirement.requirement_operator import RequirementOperator # No longer directly used here

def get_latest_patient_lab_value(patient_lab_values: List[PatientLabValue], lab_value_name: str) -> Optional[PatientLabValue]:
    """
    Retrieves the most recent PatientLabValue for a specific lab_value_name.
    """
    relevant_values = [
        plv for plv in patient_lab_values if plv.lab_value and plv.lab_value.name == lab_value_name
    ]
    if not relevant_values:
        return None
    return max(relevant_values, key=lambda plv: plv.datetime)

def get_patient_lab_values_in_timeframe(
    patient_lab_values: List[PatientLabValue],
    lab_value_name: str,
    days_min: Optional[int] = None,
    days_max: Optional[int] = None,
) -> List[PatientLabValue]:
    """
    Retrieves PatientLabValues for a specific lab_value_name within a given timeframe.
    Timeframe is relative to the current date.
    days_min: e.g., -7 for 7 days ago (start of timeframe)
    days_max: e.g., 0 for today (end of timeframe)
    """
    relevant_values = [
        plv for plv in patient_lab_values if plv.lab_value and plv.lab_value.name == lab_value_name
    ]
    if not relevant_values:
        return []

    today = datetime.now().date()
    filtered_values = []

    for plv in relevant_values:
        plv_date = plv.datetime.date()
        in_timeframe = True
        if days_min is not None:
            start_date = today + timedelta(days=days_min)
            if plv_date < start_date:
                in_timeframe = False
        if days_max is not None:
            end_date = today + timedelta(days=days_max)
            if plv_date > end_date:
                in_timeframe = False
        if in_timeframe:
            filtered_values.append(plv)
    return filtered_values


def lab_latest_numeric_increased(
    input_links: "RequirementLinks", # Changed
    requirement: Requirement,
    operator_kwargs: Dict[str, Any]
) -> bool:
    """
    Returns True if the latest numeric lab value for all required lab values is above the normal maximum range; otherwise returns False.
    
    Returns False if any required lab value is missing, the latest value is unavailable, or the value does not exceed the normal maximum.
    """
    patient_lab_values = input_links.patient_lab_values
    if not requirement.lab_values.exists():
        return False
    for lab_value_model in requirement.lab_values.all():
        latest_plv = get_latest_patient_lab_value(patient_lab_values, lab_value_model.name)
        if not (latest_plv and latest_plv.value is not None and latest_plv.lab_value):
            return False
        patient_context = input_links.get_first_patient()
        normal_range = latest_plv.lab_value.get_normal_range(
            age=patient_context.age() if patient_context else None,
            gender=patient_context.gender if patient_context else None
        )
        if normal_range.get("max") is None or latest_plv.value <= normal_range["max"]:
            return False
    return True

def lab_latest_numeric_decreased(
    input_links: "RequirementLinks", # Changed
    requirement: Requirement,
    operator_kwargs: Dict[str, Any]
) -> bool:
    """
    Returns True if the latest numeric lab value for all required lab values is below the normal minimum range.
    
    Returns False if any latest value is missing, lacks a normal range, or is not below the minimum.
    """
    patient_lab_values = input_links.patient_lab_values
    if not requirement.lab_values.exists(): # Changed
        return False
    for lab_value_model in requirement.lab_values.all():
        latest_plv = get_latest_patient_lab_value(patient_lab_values, lab_value_model.name)
        if not (latest_plv and latest_plv.value is not None and latest_plv.lab_value): # Added check for latest_plv.lab_value
            return False
        patient_context = input_links.get_first_patient()
        normal_range = latest_plv.lab_value.get_normal_range(
            age=patient_context.age() if patient_context else None, 
            gender=patient_context.gender if patient_context else None
        )
        if normal_range.get("min") is None or latest_plv.value >= normal_range["min"]:
            return False
    return True

def lab_latest_numeric_normal(
    input_links: "RequirementLinks", # Changed
    requirement: Requirement,
    operator_kwargs: Dict[str, Any]
) -> bool:
    """
    Returns True if the latest numeric lab value for all required lab values is within the normal range.
    
    Returns False if any latest value is missing, lacks a normal range, or falls outside the normal range.
    """
    patient_lab_values = input_links.patient_lab_values
    if not requirement.lab_values.exists(): # Changed
        return False
    for lab_value_model in requirement.lab_values.all():
        latest_plv = get_latest_patient_lab_value(patient_lab_values, lab_value_model.name)
        if not (latest_plv and latest_plv.value is not None and latest_plv.lab_value): # Added check for latest_plv.lab_value
            return False
        patient_context = input_links.get_first_patient()
        normal_range = latest_plv.lab_value.get_normal_range(
            age=patient_context.age() if patient_context else None, 
            gender=patient_context.gender if patient_context else None
        )
        min_val = normal_range.get("min")
        max_val = normal_range.get("max")
        if min_val is None or max_val is None:
            return False
        if not (min_val <= latest_plv.value <= max_val):
            return False
    return True

def lab_latest_numeric_lower_than_value(
    input_links: "RequirementLinks", # Changed
    requirement: Requirement,
    operator_kwargs: Dict[str, Any]
) -> bool:
    """
    Returns True if the latest numeric lab value for all required lab values is lower than the specified threshold.
    
    Returns False if any latest value is missing or not below the requirement's numeric value.
    """
    patient_lab_values = input_links.patient_lab_values
    if not requirement.lab_values.exists() or requirement.numeric_value is None: # Changed
        return False
    for lab_value_model in requirement.lab_values.all():
        latest_plv = get_latest_patient_lab_value(patient_lab_values, lab_value_model.name)
        if not (latest_plv and latest_plv.value is not None):
            return False
        if not (latest_plv.value < requirement.numeric_value):
            return False
    return True

def lab_latest_numeric_greater_than_value(
    input_links: "RequirementLinks", # Changed
    requirement: Requirement,
    operator_kwargs: Dict[str, Any]
) -> bool:
    """
    Returns True if the latest numeric lab value for all required lab values is greater than the specified threshold.
    
    Returns False if any latest value is missing or not greater than the requirement's numeric value.
    """
    patient_lab_values = input_links.patient_lab_values
    if not requirement.lab_values.exists() or requirement.numeric_value is None: # Changed
        return False
    for lab_value_model in requirement.lab_values.all():
        latest_plv = get_latest_patient_lab_value(patient_lab_values, lab_value_model.name)
        if not (latest_plv and latest_plv.value is not None):
            return False
        if not (latest_plv.value > requirement.numeric_value):
            return False
    return True

# --- Operators with timeframe ---

def lab_latest_numeric_increased_factor_in_timeframe(
    input_links: "RequirementLinks", # Changed
    requirement: Requirement,
    operator_kwargs: Dict[str, Any]
) -> bool:
    """
    Checks if the numeric lab value has increased by a certain factor within a timeframe.
    The factor is defined in requirement.numeric_value.
    The timeframe (in days relative to now) is defined by
    requirement.numeric_value_min (start, e.g., -7 for 7 days ago) and
    requirement.numeric_value_max (end, e.g., 0 for today).
    This operator checks if *any* value in the timeframe is increased by the factor compared to the value *at the start* of the timeframe.
    More sophisticated checks (e.g., sustained increase, comparison to earliest value in overall history) might require different logic.
    """
    patient_lab_values = input_links.patient_lab_values
    if (not requirement.lab_values.exists() or
        requirement.numeric_value is None or
        requirement.numeric_value_min is None or
        requirement.numeric_value_max is None):
        return False

    factor = requirement.numeric_value
    days_min = int(requirement.numeric_value_min) # Start of timeframe
    days_max = int(requirement.numeric_value_max) # End of timeframe

    for lab_value_model in requirement.lab_values.all():
        # Get all values for this lab type, not just within the timeframe initially
        all_lab_values_for_type = sorted(
            [plv for plv in patient_lab_values if plv.lab_value and plv.lab_value.name == lab_value_model.name and plv.value is not None],
            key=lambda plv: plv.datetime
        )

        if not all_lab_values_for_type:
            continue

        # Determine the reference value: the one closest to, but before or at, the start of the timeframe
        start_of_timeframe_date = (datetime.now() + timedelta(days=days_min)).date()
        reference_plv = None
        for plv in reversed(all_lab_values_for_type):
            if plv.datetime.date() <= start_of_timeframe_date:
                reference_plv = plv
                break
        
        if reference_plv is None and all_lab_values_for_type: # if no value before timeframe, use earliest available
             reference_plv = all_lab_values_for_type[0]


        if reference_plv and reference_plv.value is not None:
            reference_value = reference_plv.value
            # Now get values strictly within the defined timeframe
            values_in_timeframe = get_patient_lab_values_in_timeframe(
                patient_lab_values, lab_value_model.name, days_min, days_max
            )
            for plv_in_frame in values_in_timeframe:
                if plv_in_frame.value is not None and plv_in_frame.value > (reference_value * factor):
                    return True # Found a value increased by the factor
    return False

def lab_latest_numeric_decreased_factor_in_timeframe(
    input_links: "RequirementLinks",
    requirement: Requirement,
    operator_kwargs: Dict[str, Any]
) -> bool:
    """
    Checks if the numeric lab value has decreased by a certain factor within a timeframe.
    Factor is in requirement.numeric_value. Timeframe in numeric_value_min/max.
    Compares values in timeframe to the value at the start of (or just before) the timeframe.
    """
    patient_lab_values = input_links.patient_lab_values
    if (not requirement.lab_values.exists() or
        requirement.numeric_value is None or
        requirement.numeric_value_min is None or
        requirement.numeric_value_max is None):
        return False

    factor = requirement.numeric_value
    days_min = int(requirement.numeric_value_min)
    days_max = int(requirement.numeric_value_max)

    for lab_value_model in requirement.lab_values.all():
        all_lab_values_for_type = sorted(
            [plv for plv in patient_lab_values if plv.lab_value and plv.lab_value.name == lab_value_model.name and plv.value is not None],
            key=lambda plv: plv.datetime
        )
        if not all_lab_values_for_type:
            continue

        start_of_timeframe_date = (datetime.now() + timedelta(days=days_min)).date()
        reference_plv = None
        for plv in reversed(all_lab_values_for_type):
            if plv.datetime.date() <= start_of_timeframe_date:
                reference_plv = plv
                break
        if reference_plv is None and all_lab_values_for_type:
            reference_plv = all_lab_values_for_type[0]

        if reference_plv and reference_plv.value is not None:
            reference_value = reference_plv.value
            if reference_value == 0: # Avoid division by zero or issues with zero reference
                continue 
            values_in_timeframe = get_patient_lab_values_in_timeframe(
                patient_lab_values, lab_value_model.name, days_min, days_max
            )
            for plv_in_frame in values_in_timeframe:
                if plv_in_frame.value is not None and plv_in_frame.value < (reference_value / factor): # Decreased by factor
                    return True
    return False

def lab_latest_numeric_normal_in_timeframe(
    input_links: "RequirementLinks",
    requirement: Requirement,
    operator_kwargs: Dict[str, Any]
) -> bool:
    """
    Checks if any numeric lab value within a timeframe is within its normal range.
    Timeframe in requirement.numeric_value_min/max.
    """
    patient_lab_values = input_links.patient_lab_values
    if (not requirement.lab_values.exists() or
        requirement.numeric_value_min is None or
        requirement.numeric_value_max is None):
        return False

    days_min = int(requirement.numeric_value_min)
    days_max = int(requirement.numeric_value_max)
    patient_context = input_links.get_first_patient()

    for lab_value_model in requirement.lab_values.all():
        values_in_timeframe = get_patient_lab_values_in_timeframe(
            patient_lab_values, lab_value_model.name, days_min, days_max
        )
        for plv in values_in_timeframe:
            if plv.value is not None and plv.lab_value:
                normal_range = plv.lab_value.get_normal_range(
                    age=patient_context.age() if patient_context else None,
                    gender=patient_context.gender if patient_context else None
                )
                min_val = normal_range.get("min")
                max_val = normal_range.get("max")
                if min_val is not None and max_val is not None:
                    if min_val <= plv.value <= max_val:
                        return True
    return False

def lab_latest_numeric_lower_than_value_in_timeframe(
    input_links: "RequirementLinks",
    requirement: Requirement,
    operator_kwargs: Dict[str, Any]
) -> bool:
    """
    Checks if any numeric lab value within a timeframe is lower than requirement.numeric_value.
    Timeframe in requirement.numeric_value_min/max.
    """
    patient_lab_values = input_links.patient_lab_values
    if (not requirement.lab_values.exists() or
        requirement.numeric_value is None or
        requirement.numeric_value_min is None or
        requirement.numeric_value_max is None):
        return False

    threshold = requirement.numeric_value
    days_min = int(requirement.numeric_value_min)
    days_max = int(requirement.numeric_value_max)

    for lab_value_model in requirement.lab_values.all():
        values_in_timeframe = get_patient_lab_values_in_timeframe(
            patient_lab_values, lab_value_model.name, days_min, days_max
        )
        for plv in values_in_timeframe:
            if plv.value is not None and plv.value < threshold:
                return True
    return False

def lab_latest_numeric_greater_than_value_in_timeframe(
    input_links: "RequirementLinks",
    requirement: Requirement,
    operator_kwargs: Dict[str, Any]
) -> bool:
    """
    Checks if any numeric lab value within a timeframe is greater than requirement.numeric_value.
    Timeframe in requirement.numeric_value_min/max.
    """
    patient_lab_values = input_links.patient_lab_values
    if (not requirement.lab_values.exists() or
        requirement.numeric_value is None or
        requirement.numeric_value_min is None or
        requirement.numeric_value_max is None):
        return False

    threshold = requirement.numeric_value
    days_min = int(requirement.numeric_value_min)
    days_max = int(requirement.numeric_value_max)

    for lab_value_model in requirement.lab_values.all():
        values_in_timeframe = get_patient_lab_values_in_timeframe(
            patient_lab_values, lab_value_model.name, days_min, days_max
        )
        for plv in values_in_timeframe:
            if plv.value is not None and plv.value > threshold:
                return True
    return False

# --- Categorical Operators ---
# Assuming PatientLabValue has a 'value_string' attribute for categorical results.

def lab_latest_categorical_match(
    input_links: "RequirementLinks",
    requirement: Requirement,
    operator_kwargs: Dict[str, Any]
) -> bool:
    """
    Checks if the latest categorical lab value matches requirement.string_value.
    """
    patient_lab_values = input_links.patient_lab_values
    if not requirement.lab_values.exists() or requirement.string_value is None:
        return False

    match_string = requirement.string_value
    for lab_value_model in requirement.lab_values.all():
        latest_plv = get_latest_patient_lab_value(patient_lab_values, lab_value_model.name)
        # Ensure latest_plv has value_str attribute
        if latest_plv and hasattr(latest_plv, 'value_str') and latest_plv.value_str is not None: # Changed value_string to value_str
            if latest_plv.value_str == match_string:
                return True
    return False

def lab_latest_categorical_match_substring(
    input_links: "RequirementLinks",
    requirement: Requirement,
    operator_kwargs: Dict[str, Any]
) -> bool:
    """
    Checks if requirement.string_value is a substring of the latest categorical lab value.
    """
    patient_lab_values = input_links.patient_lab_values
    if not requirement.lab_values.exists() or requirement.string_value is None:
        return False

    substring = requirement.string_value
    for lab_value_model in requirement.lab_values.all():
        latest_plv = get_latest_patient_lab_value(patient_lab_values, lab_value_model.name)
        if latest_plv and hasattr(latest_plv, 'value_str') and latest_plv.value_str is not None: # Changed value_string to value_str
            if substring in latest_plv.value_str:
                return True
    return False

def lab_latest_categorical_match_regex(
    input_links: "RequirementLinks",
    requirement: Requirement,
    operator_kwargs: Dict[str, Any]
) -> bool:
    """
    Checks if the latest categorical lab value matches regex in requirement.string_value.
    """
    patient_lab_values = input_links.patient_lab_values
    if not requirement.lab_values.exists() or requirement.string_value is None:
        return False

    regex_pattern = requirement.string_value
    try:
        compiled_regex = re.compile(regex_pattern)
    except re.error:
        return False # Invalid regex

    for lab_value_model in requirement.lab_values.all():
        latest_plv = get_latest_patient_lab_value(patient_lab_values, lab_value_model.name)
        if latest_plv and hasattr(latest_plv, 'value_str') and latest_plv.value_str is not None: # Changed value_string to value_str
            if compiled_regex.search(latest_plv.value_str):
                return True
    return False

# --- Categorical Operators with Timeframe ---

def lab_latest_categorical_match_in_timeframe(
    input_links: "RequirementLinks",
    requirement: Requirement,
    operator_kwargs: Dict[str, Any]
) -> bool:
    """
    Checks if any categorical lab value in timeframe matches requirement.string_value.
    Timeframe in requirement.numeric_value_min/max.
    """
    patient_lab_values = input_links.patient_lab_values
    if (not requirement.lab_values.exists() or
        requirement.string_value is None or
        requirement.numeric_value_min is None or
        requirement.numeric_value_max is None):
        return False

    match_string = requirement.string_value
    days_min = int(requirement.numeric_value_min)
    days_max = int(requirement.numeric_value_max)

    for lab_value_model in requirement.lab_values.all():
        values_in_timeframe = get_patient_lab_values_in_timeframe(
            patient_lab_values, lab_value_model.name, days_min, days_max
        )
        for plv in values_in_timeframe:
            if hasattr(plv, 'value_str') and plv.value_str is not None: # Changed value_string to value_str
                if plv.value_str == match_string:
                    return True
    return False

def lab_latest_categorical_match_substring_in_timeframe(
    input_links: "RequirementLinks",
    requirement: Requirement,
    operator_kwargs: Dict[str, Any]
) -> bool:
    """
    Checks if requirement.string_value is substring of any categorical lab value in timeframe.
    Timeframe in requirement.numeric_value_min/max.
    """
    patient_lab_values = input_links.patient_lab_values
    if (not requirement.lab_values.exists() or
        requirement.string_value is None or
        requirement.numeric_value_min is None or
        requirement.numeric_value_max is None):
        return False

    substring = requirement.string_value
    days_min = int(requirement.numeric_value_min)
    days_max = int(requirement.numeric_value_max)

    for lab_value_model in requirement.lab_values.all():
        values_in_timeframe = get_patient_lab_values_in_timeframe(
            patient_lab_values, lab_value_model.name, days_min, days_max
        )
        for plv in values_in_timeframe:
            if hasattr(plv, 'value_str') and plv.value_str is not None: # Changed value_string to value_str
                if substring in plv.value_str:
                    return True
    return False

def lab_latest_categorical_match_regex_in_timeframe(
    input_links: "RequirementLinks",
    requirement: Requirement,
    operator_kwargs: Dict[str, Any]
) -> bool:
    """
    Checks if any categorical lab value in timeframe matches regex in requirement.string_value.
    Timeframe in requirement.numeric_value_min/max.
    """
    patient_lab_values = input_links.patient_lab_values
    if (not requirement.lab_values.exists() or
        requirement.string_value is None or
        requirement.numeric_value_min is None or
        requirement.numeric_value_max is None):
        return False

    regex_pattern = requirement.string_value
    try:
        compiled_regex = re.compile(regex_pattern)
    except re.error:
        return False

    days_min = int(requirement.numeric_value_min)
    days_max = int(requirement.numeric_value_max)

    for lab_value_model in requirement.lab_values.all():
        values_in_timeframe = get_patient_lab_values_in_timeframe(
            patient_lab_values, lab_value_model.name, days_min, days_max
        )
        for plv in values_in_timeframe:
            if hasattr(plv, 'value_str') and plv.value_str is not None: # Changed value_string to value_str
                if compiled_regex.search(plv.value_str):
                    return True
    return False


# Mapping operator names to functions
LAB_VALUE_OPERATOR_FUNCTIONS: Dict[str, Callable] = {
    "lab_latest_numeric_increased": lab_latest_numeric_increased,
    "lab_latest_numeric_decreased": lab_latest_numeric_decreased,
    "lab_latest_numeric_normal": lab_latest_numeric_normal,
    "lab_latest_numeric_lower_than_value": lab_latest_numeric_lower_than_value,
    "lab_latest_numeric_greater_than_value": lab_latest_numeric_greater_than_value,

    # Aliases for backward compatibility, pointing to timeframe versions
    "lab_latest_numeric_increased_factor": lab_latest_numeric_increased_factor_in_timeframe,
    "lab_latest_numeric_decreased_factor": lab_latest_numeric_decreased_factor_in_timeframe,

    "lab_latest_numeric_increased_factor_in_timeframe": lab_latest_numeric_increased_factor_in_timeframe,
    "lab_latest_numeric_decreased_factor_in_timeframe": lab_latest_numeric_decreased_factor_in_timeframe,
    "lab_latest_numeric_normal_in_timeframe": lab_latest_numeric_normal_in_timeframe,
    "lab_latest_numeric_lower_than_value_in_timeframe": lab_latest_numeric_lower_than_value_in_timeframe,
    "lab_latest_numeric_greater_than_value_in_timeframe": lab_latest_numeric_greater_than_value_in_timeframe,

    "lab_latest_categorical_match": lab_latest_categorical_match,
    "lab_latest_categorical_match_substring": lab_latest_categorical_match_substring,
    "lab_latest_categorical_match_regex": lab_latest_categorical_match_regex,
    "lab_latest_categorical_match_in_timeframe": lab_latest_categorical_match_in_timeframe,
    "lab_latest_categorical_match_substring_in_timeframe": lab_latest_categorical_match_substring_in_timeframe,
    "lab_latest_categorical_match_regex_in_timeframe": lab_latest_categorical_match_regex_in_timeframe,
}
