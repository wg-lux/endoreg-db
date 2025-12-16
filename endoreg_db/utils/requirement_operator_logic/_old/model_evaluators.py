import calendar
import datetime
from datetime import timedelta
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from endoreg_db.models.requirement.requirement import Requirement
    from endoreg_db.utils.links.requirement_link import RequirementLinks
    # from endoreg_db.models import Unit # Potentially needed for dynamic unit handling


# Helper function to check if a date is within the timeframe specified by a Requirement
def _normalize_timeframe_unit(requirement: "Requirement") -> str | None:
    """Derives a canonical timeframe unit string from the requirement."""
    if not requirement.unit:
        return None

    name = (requirement.unit.name or "").strip().lower()
    abbreviation = (requirement.unit.abbreviation or "").strip().lower()

    unit_aliases = {
        "hour": "hours",
        "hours": "hours",
        "h": "hours",
        "day": "days",
        "days": "days",
        "d": "days",
        "week": "weeks",
        "weeks": "weeks",
        "w": "weeks",
        "month": "months",
        "months": "months",
        "m": "months",
        "year": "years",
        "years": "years",
        "y": "years",
    }

    if name in unit_aliases:
        return unit_aliases[name]
    if abbreviation in unit_aliases:
        return unit_aliases[abbreviation]
    return None


def _add_months(base_date: datetime.date, months: int) -> datetime.date:
    """Shifts ``base_date`` by the given number of months, clamping the day when needed."""
    if months == 0:
        return base_date

    total_months = base_date.month - 1 + months
    year = base_date.year + total_months // 12
    month = total_months % 12 + 1
    day = min(base_date.day, calendar.monthrange(year, month)[1])
    return base_date.replace(year=year, month=month, day=day)


def _shift_date_by_unit(base_date: datetime.date, value: int, unit: str) -> datetime.date:
    """Returns ``base_date`` shifted by ``value`` units using the supported unit set.

    Hour-level offsets are not supported because ``base_date`` is a ``date``
    object without time information; callers must switch to datetime-aware
    handling before requesting hourly shifts.
    """
    if unit == "days":
        return base_date + timedelta(days=value)
    if unit == "hours":
        raise NotImplementedError("Hour-level timeframe comparisons require datetime-aware inputs; _shift_date_by_unit only operates on date objects.")
    if unit == "weeks":
        return base_date + timedelta(weeks=value)
    if unit == "months":
        return _add_months(base_date, value)
    if unit == "years":
        return _add_months(base_date, value * 12)
    raise NotImplementedError(f"Timeframe unit '{unit}' is not supported.")


def _is_date_in_timeframe(date_to_check: datetime.date | None, requirement: "Requirement") -> bool:
    """
    Checks if a given date falls within the timeframe specified by a Requirement.

    The timeframe is defined by `numeric_value_min` and `numeric_value_max` on the
    Requirement, interpreted relative to the current date.

    Currently supports day/week/month/year windows. Hour-level units are
    explicitly rejected because the timeframe calculations operate on dates
    rather than datetimes.

    Args:
        date_to_check: The date to evaluate. If None, returns False.
        requirement: The Requirement instance containing timeframe definitions
                     (unit, numeric_value_min, numeric_value_max).

    Returns:
        True if the date_to_check is within the defined timeframe, False otherwise.
        Returns False if date_to_check is None or if the requirement lacks
        necessary timeframe information (unit, min/max values).

    Raises:
        NotImplementedError: If the requirement.unit resolves to an unsupported
        unit (e.g. hours) or cannot be normalised.
    """
    if date_to_check is None:
        return False
    if requirement.numeric_value_min is None or requirement.numeric_value_max is None:
        return False  # Not enough information for timeframe evaluation

    unit = _normalize_timeframe_unit(requirement)
    if not unit:
        raise NotImplementedError("Timeframe unit could not be resolved from requirement's Unit name/abbreviation.")

    today = datetime.date.today()
    timeframe_start_delta = int(requirement.numeric_value_min)
    timeframe_end_delta = int(requirement.numeric_value_max)

    start_date_bound = _shift_date_by_unit(today, timeframe_start_delta, unit)
    end_date_bound = _shift_date_by_unit(today, timeframe_end_delta, unit)

    if start_date_bound > end_date_bound:
        start_date_bound, end_date_bound = end_date_bound, start_date_bound

    return start_date_bound <= date_to_check <= end_date_bound


def _evaluate_models_match_any(requirement_links: "RequirementLinks", input_links: "RequirementLinks", **kwargs) -> bool:
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
    requirement: "Requirement",  # Explicitly pass Requirement
    **kwargs,  # Keep for consistency, though 'requirement' is the main one used here
) -> bool:
    """
    Checks if any relevant model in input_links matches a model specified in
    requirement_links AND falls within the timeframe defined on the Requirement.

    Currently focuses on PatientEvent instances and their dates.
    """
    if not _has_timeframe_configuration(requirement):
        return False

    active_req_links_dict = requirement_links.active()
    if not active_req_links_dict:
        # If the Requirement itself doesn't specify any models to match (e.g., requirement.events is empty),
        # then it's vacuously true that "any" of these (non-existent) required models are matched.
        # The timeframe aspect becomes irrelevant if no specific models are being checked.
        return True

    # --- Handle PatientEvents ---
    # Check if the requirement is concerned with events
    if requirement_links.events:  # This list contains Event model instances
        required_event_models = set(requirement_links.events)  # Target Event models from the Requirement

        # input_links.patient_events contains PatientEvent instances provided as input
        for patient_event_instance in input_links.patient_events:
            # Check if the event of the current PatientEvent instance is one of the target events
            if patient_event_instance.event in required_event_models:
                # If it is, check if this PatientEvent's date is within the timeframe
                if _is_date_in_timeframe(patient_event_instance.date, requirement):
                    return True  # Found a matching event within the timeframe

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


def _evaluate_models_match_all_in_timeframe(
    requirement_links: "RequirementLinks", input_links: "RequirementLinks", requirement: "Requirement", **kwargs
) -> bool:
    if not _evaluate_models_match_all(requirement_links, input_links, **kwargs):
        return False

    if not requirement_links.events:
        return True

    if not _has_timeframe_configuration(requirement):
        return False

    required_events = set(requirement_links.events)
    patient_events = getattr(input_links, "patient_events", [])
    if not patient_events:
        return False

    for event in required_events:
        found_in_timeframe = False
        for patient_event in patient_events:
            if getattr(patient_event, "event", None) == event:
                if _is_date_in_timeframe(getattr(patient_event, "date", None), requirement):
                    found_in_timeframe = True
                    break
        if not found_in_timeframe:
            return False
    return True

    if requirement.unit is None:
        return False


def _evaluate_models_match_none_in_timeframe(
    requirement_links: "RequirementLinks", input_links: "RequirementLinks", requirement: "Requirement", **kwargs
) -> bool:
    if not requirement_links.events:
        return True

    if not _has_timeframe_configuration(requirement):
        return False

    required_events = set(requirement_links.events)
    for patient_event in getattr(input_links, "patient_events", []):
        if getattr(patient_event, "event", None) in required_events:
            if _is_date_in_timeframe(getattr(patient_event, "date", None), requirement):
                return False
    return True


def _evaluate_models_match_all(requirement_links: "RequirementLinks", input_links: "RequirementLinks", **kwargs) -> bool:
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
    active_req_links = requirement_links.active()  # Get dict of non-empty lists from requirement

    if not active_req_links:  # If the requirement specifies no actual items to link
        return True  # Vacuously true, as there are no conditions to fail

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


def _evaluate_models_match_none(requirement_links: "RequirementLinks", input_links: "RequirementLinks", **kwargs) -> bool:
    """Returns True when no required models are present in the input links."""
    active_req_links = requirement_links.active()
    if not active_req_links:
        return True

    for link_category_name, req_items_list in active_req_links.items():
        input_items_list = getattr(input_links, link_category_name, [])
        if not input_items_list:
            continue
        try:
            if set(req_items_list) & set(input_items_list):
                return False
        except TypeError:
            for req_item in req_items_list:
                if req_item in input_items_list:
                    return False
    return True


def _count_matching_models(
    requirement_links: "RequirementLinks",
    input_links: "RequirementLinks",
) -> int:
    """Counts how many required models are present in the input links."""
    active_req_links = requirement_links.active()
    if not active_req_links:
        return 0

    match_count = 0
    for link_category_name, req_items_list in active_req_links.items():
        input_items_list = getattr(input_links, link_category_name, [])
        if not input_items_list:
            continue
        try:
            match_count += len(set(req_items_list) & set(input_items_list))
        except TypeError:
            for req_item in req_items_list:
                if req_item in input_items_list:
                    match_count += 1
    return match_count


def _resolve_expected_count(requirement: "Requirement") -> int | None:
    """Extracts the expected count from numeric fields on the requirement."""
    if requirement.numeric_value is not None:
        return int(requirement.numeric_value)
    if requirement.numeric_value_min is not None:
        return int(requirement.numeric_value_min)
    return None


def _has_timeframe_configuration(requirement: "Requirement") -> bool:
    """Checks whether timeframe boundaries and unit are configured."""
    return (
        requirement.unit is not None
        and requirement.numeric_value_min is not None
        and requirement.numeric_value_max is not None
        and _normalize_timeframe_unit(requirement) is not None
    )


def _count_matching_events_in_timeframe(
    requirement_links: "RequirementLinks",
    input_links: "RequirementLinks",
    requirement: "Requirement",
) -> int:
    """Counts required events that have a matching patient event within the timeframe."""
    required_events = set(requirement_links.events)
    if not required_events:
        return 0

    patient_events = getattr(input_links, "patient_events", [])
    if not patient_events:
        return 0

    matched_events = 0
    for event in required_events:
        for patient_event in patient_events:
            if getattr(patient_event, "event", None) != event:
                continue
            if _is_date_in_timeframe(getattr(patient_event, "date", None), requirement):
                matched_events += 1
                break
    return matched_events


def _evaluate_models_match_n(requirement_links: "RequirementLinks", input_links: "RequirementLinks", requirement: "Requirement", **kwargs) -> bool:
    expected = _resolve_expected_count(requirement)
    if expected is None:
        return False
    return _count_matching_models(requirement_links, input_links) == expected


def _evaluate_models_match_n_or_more(requirement_links: "RequirementLinks", input_links: "RequirementLinks", requirement: "Requirement", **kwargs) -> bool:
    threshold = _resolve_expected_count(requirement)
    if threshold is None:
        return False
    return _count_matching_models(requirement_links, input_links) >= max(threshold, 0)


def _evaluate_models_match_n_or_less(requirement_links: "RequirementLinks", input_links: "RequirementLinks", requirement: "Requirement", **kwargs) -> bool:
    limit = _resolve_expected_count(requirement)
    if limit is None:
        return False
    return _count_matching_models(requirement_links, input_links) <= limit


def _evaluate_models_match_count_in_range(requirement_links: "RequirementLinks", input_links: "RequirementLinks", requirement: "Requirement", **kwargs) -> bool:
    if requirement.numeric_value_min is None or requirement.numeric_value_max is None:
        return False

    lower = int(requirement.numeric_value_min)
    upper = int(requirement.numeric_value_max)
    if lower > upper:
        lower, upper = upper, lower

    match_count = _count_matching_models(requirement_links, input_links)
    return lower <= match_count <= upper


def _evaluate_models_match_n_in_timeframe(requirement_links: "RequirementLinks", input_links: "RequirementLinks", requirement: "Requirement", **kwargs) -> bool:
    if not _has_timeframe_configuration(requirement):
        return False

    expected = _resolve_expected_count(requirement)
    if expected is None:
        return False

    return _count_matching_events_in_timeframe(requirement_links, input_links, requirement) == expected


def _evaluate_models_match_n_or_more_in_timeframe(
    requirement_links: "RequirementLinks", input_links: "RequirementLinks", requirement: "Requirement", **kwargs
) -> bool:
    if not _has_timeframe_configuration(requirement):
        return False

    threshold = _resolve_expected_count(requirement)
    if threshold is None:
        return False

    return _count_matching_events_in_timeframe(requirement_links, input_links, requirement) >= max(threshold, 0)


def _evaluate_models_match_n_or_less_in_timeframe(
    requirement_links: "RequirementLinks", input_links: "RequirementLinks", requirement: "Requirement", **kwargs
) -> bool:
    if not _has_timeframe_configuration(requirement):
        return False

    limit = _resolve_expected_count(requirement)
    if limit is None:
        return False

    return _count_matching_events_in_timeframe(requirement_links, input_links, requirement) <= limit


def _evaluate_age_gte(requirement_links: "RequirementLinks", input_links: "RequirementLinks", requirement: "Requirement", **kwargs) -> bool:
    """
    Checks if any patient in the input has an age greater than or equal to the requirement's numeric_value.

    Args:
        requirement_links: The RequirementLinks object from the Requirement model (not used for age checks).
        input_links: The aggregated RequirementLinks object from the input arguments.
        requirement: The Requirement instance containing the minimum age in numeric_value.
        **kwargs: Additional keyword arguments, should contain 'original_input_args' with the original Patient instances.

    Returns:
        True if any patient in the input has an age >= requirement.numeric_value, False otherwise.
    """
    import logging

    from endoreg_db.models.administration.person.patient import Patient

    logger = logging.getLogger(__name__)

    if requirement.numeric_value is None:
        logger.debug("age_gte: requirement.numeric_value is None, returning False")
        return False  # Cannot evaluate without a minimum age requirement

    min_age = requirement.numeric_value
    logger.debug(f"age_gte: Checking if any patient has age >= {min_age}")

    # Check if we have Patient instances in the original_input_args
    original_args = kwargs.get("original_input_args", [])
    logger.debug(f"age_gte: Found {len(original_args)} original input arguments: {[type(arg).__name__ for arg in original_args]}")

    for i, arg in enumerate(original_args):
        logger.debug(f"age_gte: Checking argument {i}: {type(arg).__name__}")
        if isinstance(arg, Patient):
            patient_age = arg.age()
            logger.debug(f"age_gte: Patient {arg} has age {patient_age}, comparing with min_age {min_age}")
            if patient_age is not None and patient_age >= min_age:
                logger.debug(f"age_gte: Patient age {patient_age} >= {min_age}, returning True")
                return True
            else:
                logger.debug(f"age_gte: Patient age {patient_age} < {min_age} or is None")
        # Handle QuerySets of patients
        elif hasattr(arg, "model") and issubclass(arg.model, Patient):
            logger.debug(f"age_gte: Found Patient QuerySet with {arg.count()} patients")
            for patient in arg:
                patient_age = patient.age()
                logger.debug(f"age_gte: Patient {patient} has age {patient_age}, comparing with min_age {min_age}")
                if patient_age is not None and patient_age >= min_age:
                    logger.debug(f"age_gte: Patient age {patient_age} >= {min_age}, returning True")
                    return True
        else:
            logger.debug(f"age_gte: Argument {i} is not a Patient or Patient QuerySet: {type(arg)}")

    logger.debug(f"age_gte: No patient found with age >= {min_age}, returning False")
    return False


def _evaluate_age_lte(requirement_links: "RequirementLinks", input_links: "RequirementLinks", requirement: "Requirement", **kwargs) -> bool:
    """
    Checks if any patient in the input has an age less than or equal to the requirement's numeric_value.

    Args:
        requirement_links: The RequirementLinks object from the Requirement model (not used for age checks).
        input_links: The aggregated RequirementLinks object from the input arguments.
        requirement: The Requirement instance containing the maximum age in numeric_value.
        **kwargs: Additional keyword arguments, should contain 'original_input_args' with the original Patient instances.

    Returns:
        True if any patient in the input has an age <= requirement.numeric_value, False otherwise.
    """
    from endoreg_db.models.administration.person.patient import Patient

    if requirement.numeric_value is None:
        return False  # Cannot evaluate without a maximum age requirement

    max_age = requirement.numeric_value

    # Check if we have Patient instances in the original_input_args
    original_args = kwargs.get("original_input_args", [])
    for arg in original_args:
        if isinstance(arg, Patient):
            patient_age = arg.age()
            if patient_age is not None and patient_age <= max_age:
                return True
        # Handle QuerySets of patients
        elif hasattr(arg, "model") and issubclass(arg.model, Patient):
            for patient in arg:
                patient_age = patient.age()
                if patient_age is not None and patient_age <= max_age:
                    return True

    return False


def dispatch_operator_evaluation(operator_name: str, requirement_links: "RequirementLinks", input_links: "RequirementLinks", **kwargs) -> bool:
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
    from endoreg_db.models.requirement.requirement import Requirement  # Runtime import for isinstance

    from .lab_value_operators import LAB_VALUE_OPERATOR_FUNCTIONS

    eval_func = None
    requirement = kwargs.get("requirement")  # Get requirement for operators that need it

    def _kwargs_without_requirement() -> dict:
        return {k: v for k, v in kwargs.items() if k != "requirement"}

    if operator_name == "models_match_any":
        eval_func = _evaluate_models_match_any
        return eval_func(requirement_links=requirement_links, input_links=input_links, **kwargs)
    elif operator_name == "models_match_all":
        eval_func = _evaluate_models_match_all
        return eval_func(requirement_links=requirement_links, input_links=input_links, **kwargs)
    elif operator_name == "models_match_none":
        eval_func = _evaluate_models_match_none
        return eval_func(requirement_links=requirement_links, input_links=input_links, **kwargs)
    elif operator_name == "models_match_any_in_timeframe":
        # 'requirement' is already extracted from kwargs via requirement = kwargs.get("requirement")
        if not isinstance(requirement, Requirement):  # Ensure requirement is present and correct type
            raise ValueError("models_match_any_in_timeframe operator requires a valid 'requirement' instance in kwargs.")
        kwargs_for_eval = _kwargs_without_requirement()
        eval_func = _evaluate_models_match_any_in_timeframe
        return eval_func(
            requirement_links=requirement_links,
            input_links=input_links,
            requirement=requirement,  # Pass the requirement instance explicitly
            **kwargs_for_eval,  # Pass the remaining kwargs
        )
    elif operator_name == "models_match_all_in_timeframe":
        if not isinstance(requirement, Requirement):
            raise ValueError("models_match_all_in_timeframe operator requires a valid 'requirement' instance in kwargs.")
        kwargs_for_eval = _kwargs_without_requirement()
        return _evaluate_models_match_all_in_timeframe(requirement_links=requirement_links, input_links=input_links, requirement=requirement, **kwargs_for_eval)
    elif operator_name == "models_match_none_in_timeframe":
        if not isinstance(requirement, Requirement):
            raise ValueError("models_match_none_in_timeframe operator requires a valid 'requirement' instance in kwargs.")
        kwargs_for_eval = _kwargs_without_requirement()
        return _evaluate_models_match_none_in_timeframe(
            requirement_links=requirement_links, input_links=input_links, requirement=requirement, **kwargs_for_eval
        )
    elif operator_name == "models_match_n":
        if not isinstance(requirement, Requirement):
            raise ValueError("models_match_n operator requires a valid 'requirement' instance in kwargs.")
        kwargs_for_eval = _kwargs_without_requirement()
        return _evaluate_models_match_n(requirement_links=requirement_links, input_links=input_links, requirement=requirement, **kwargs_for_eval)
    elif operator_name == "models_match_n_or_more":
        if not isinstance(requirement, Requirement):
            raise ValueError("models_match_n_or_more operator requires a valid 'requirement' instance in kwargs.")
        kwargs_for_eval = _kwargs_without_requirement()
        return _evaluate_models_match_n_or_more(requirement_links=requirement_links, input_links=input_links, requirement=requirement, **kwargs_for_eval)
    elif operator_name == "models_match_n_or_less":
        if not isinstance(requirement, Requirement):
            raise ValueError("models_match_n_or_less operator requires a valid 'requirement' instance in kwargs.")
        kwargs_for_eval = _kwargs_without_requirement()
        return _evaluate_models_match_n_or_less(requirement_links=requirement_links, input_links=input_links, requirement=requirement, **kwargs_for_eval)
    elif operator_name == "models_match_count_in_range":
        if not isinstance(requirement, Requirement):
            raise ValueError("models_match_count_in_range operator requires a valid 'requirement' instance in kwargs.")
        kwargs_for_eval = _kwargs_without_requirement()
        return _evaluate_models_match_count_in_range(requirement_links=requirement_links, input_links=input_links, requirement=requirement, **kwargs_for_eval)
    elif operator_name == "models_match_n_in_timeframe":
        if not isinstance(requirement, Requirement):
            raise ValueError("models_match_n_in_timeframe operator requires a valid 'requirement' instance in kwargs.")
        kwargs_for_eval = _kwargs_without_requirement()
        return _evaluate_models_match_n_in_timeframe(requirement_links=requirement_links, input_links=input_links, requirement=requirement, **kwargs_for_eval)
    elif operator_name == "models_match_n_or_more_in_timeframe":
        if not isinstance(requirement, Requirement):
            raise ValueError("models_match_n_or_more_in_timeframe operator requires a valid 'requirement' instance in kwargs.")
        kwargs_for_eval = _kwargs_without_requirement()
        return _evaluate_models_match_n_or_more_in_timeframe(
            requirement_links=requirement_links, input_links=input_links, requirement=requirement, **kwargs_for_eval
        )
    elif operator_name == "models_match_n_or_less_in_timeframe":
        if not isinstance(requirement, Requirement):
            raise ValueError("models_match_n_or_less_in_timeframe operator requires a valid 'requirement' instance in kwargs.")
        kwargs_for_eval = _kwargs_without_requirement()
        return _evaluate_models_match_n_or_less_in_timeframe(
            requirement_links=requirement_links, input_links=input_links, requirement=requirement, **kwargs_for_eval
        )
    elif operator_name in LAB_VALUE_OPERATOR_FUNCTIONS:
        if not isinstance(requirement, Requirement):  # Ensure requirement is present and correct type
            raise ValueError(f"Lab value operator '{operator_name}' requires a valid 'requirement' instance in kwargs.")

        eval_func = LAB_VALUE_OPERATOR_FUNCTIONS[operator_name]
        return eval_func(input_links=input_links, requirement=requirement, operator_kwargs=kwargs)
    elif operator_name == "age_gte":
        if not isinstance(requirement, Requirement):
            raise ValueError("age_gte operator requires a valid 'requirement' instance in kwargs.")

        # Create a new kwargs dict for the call, excluding 'requirement' to avoid passing it twice
        kwargs_for_eval = {k: v for k, v in kwargs.items() if k != "requirement"}

        return _evaluate_age_gte(requirement_links=requirement_links, input_links=input_links, requirement=requirement, **kwargs_for_eval)
    elif operator_name == "age_lte":
        if not isinstance(requirement, Requirement):
            raise ValueError("age_lte operator requires a valid 'requirement' instance in kwargs.")

        # Create a new kwargs dict for the call, excluding 'requirement' to avoid passing it twice
        kwargs_for_eval = {k: v for k, v in kwargs.items() if k != "requirement"}

        return _evaluate_age_lte(requirement_links=requirement_links, input_links=input_links, requirement=requirement, **kwargs_for_eval)
    else:
        raise NotImplementedError(f"Evaluation logic for operator '{operator_name}' is not implemented.")
