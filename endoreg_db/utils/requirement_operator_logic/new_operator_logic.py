from typing import TYPE_CHECKING, Any, Tuple, Union

if TYPE_CHECKING:
    from endoreg_db.models import Patient, PatientExamination, Requirement
    from endoreg_db.models.requirement.requirement_operator import OperatorInstructions


def fetch_input_target(
    input_object: Union["Patient", "PatientExamination"],
    operator_instructions: "OperatorInstructions",
) -> Tuple[str, Any]:
    input_target_names = operator_instructions.input_targets

    # Iterate over targets and stop with first successful fetch
    for input_target in input_target_names:
        # separate input_target using "." for nested attributes
        attributes = input_target.split(".")
        current_value = input_object
        try:
            for attr in attributes:
                current_value = getattr(current_value, attr)
            return (input_target, current_value)
        except AttributeError:
            continue  # Try next input target

    raise AttributeError(
        f"None of the input targets {input_target_names} could be resolved on the input object."
    )


def fetch_requirement_targets(
    requirement: "Requirement",
    operator_instructions: "OperatorInstructions",
):
    attribute_target_names = operator_instructions.requirement_targets
    target_values = {}

    for target_name in attribute_target_names:
        try:
            target_value = getattr(requirement, target_name)
            target_values[target_name] = target_value
        except AttributeError:
            raise AttributeError(
                f"Requirement does not have attribute '{target_name}'."
            )
        target_values[target_name] = target_value

    return target_values


def model_attribute_set_any(
    input_object: Any,
    operator_instructions: "OperatorInstructions",
    requirement: "Requirement",
) -> bool:
    input_target_name, input_value = fetch_input_target(
        input_object, operator_instructions
    )

    if not input_value:
        return False
    else:
        return True


def model_attribute_numeric_in_range(
    input_object: Any,
    operator_instructions: "OperatorInstructions",
    requirement: "Requirement",
) -> bool:
    input_target_name, input_value = fetch_input_target(
        input_object, operator_instructions
    )

    # make sure, input_value is numeric
    try:
        numeric_value = float(input_value)
    except (TypeError, ValueError):
        raise ValueError(
            f"Input value for target '{input_target_name}' is not numeric: {input_value}"
        )

    _min = requirement.numeric_value_min
    _max = requirement.numeric_value_max

    assert _min is not None and _max is not None, (
        "Numeric range requires both min and max values to be set."
    )

    return_value = _min <= numeric_value <= _max
    return return_value


def model_attribute_is_among_values(
    input_object: Any,
    operator_instructions: "OperatorInstructions",
    requirement: "Requirement",
) -> bool:
    input_target_name, input_value = fetch_input_target(
        input_object, operator_instructions
    )
    target_values = fetch_requirement_targets(requirement, operator_instructions)

    for target_name, target_values in target_values.items():
        if input_value in target_values:
            return True
    return False


REQUIREMENT_OPERATORS = {
    "model_attribute_set_any": model_attribute_set_any,
    "model_attribute_numeric_in_range": model_attribute_numeric_in_range,
    "model_attribute_is_among_values": model_attribute_is_among_values,
}
