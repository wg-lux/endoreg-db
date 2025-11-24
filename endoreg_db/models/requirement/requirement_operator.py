from logging import getLogger  # Added logger
from typing import TYPE_CHECKING, Any, Dict, List, Union

from django.db import models
from pydantic import BaseModel

# see how operator evaluation function is fetched, add to docs #TODO
# endoreg_db/utils/requirement_operator_logic/model_evaluators.py

if TYPE_CHECKING:
    from endoreg_db.utils.links.requirement_link import RequirementLinks

    from .requirement import Requirement  # Added Requirement import for type hint

logger = getLogger(__name__)  # Added logger instance


class OperatorInstructions(BaseModel):
    input_targets: List[str] = []
    requirement_targets: List[str] = []
    kwargs: Dict[str, Union[str, int, float, bool]] = {}
    args: List[Union[str, int, float, bool]] = []


def _parse_target_attributes(raw: str):
    target_attributes_list = [_.strip() for _ in raw.split(";") if _.strip()]

    input_targets = []
    kwargs: Dict[str, Union[str, int, float, bool]] = {}
    args: List[Union[str, int, float, bool]] = []

    valid_prefixes = [
        "!",  # Requirement target
        "?",  # Input target
        "$",  # Keyword argument, keyword and value separated by ":"
        "@",  # Positional argument
    ]

    for entry in target_attributes_list:
        _prefix = entry[0]

        if _prefix in valid_prefixes:
            _attribute = entry[1:].strip()
            if _prefix == "!":
                # Requirement target
                input_targets.append(_attribute)
            elif _prefix == "?":
                # Input target
                input_targets.append(_attribute)
            elif _prefix == "$":
                # Keyword argument
                if ":" in _attribute:
                    key, value = _attribute.split(":", 1)
                    kwargs[key.strip()] = value.strip()
                else:
                    raise ValueError(f"Invalid keyword argument format in target_attributes: '{entry}'. Expected format is '$key:value'.")
            elif _prefix == "@":
                # Positional argument
                args.append(_attribute)
        else:
            raise ValueError(f"Invalid prefix '{_prefix}' in target_attributes entry: '{entry}'. Valid prefixes are {valid_prefixes}.")
    return OperatorInstructions(
        input_targets=input_targets,
        requirement_targets=input_targets,
        kwargs=kwargs,
        args=args,
    )


def _validate_requirement_targets(instance: "Requirement") -> bool:
    """Ensures requirement fixtures declare at least one target attribute."""
    if not instance.target_attributes:
        raise ValueError(f"Requirement '{instance.name}' must declare at least one target attribute.")
    _req_target_obj = _parse_target_attributes(instance.target_attributes)
    return True


class RequirementOperatorManager(models.Manager):
    def get_by_natural_key(self, name):
        """
        Retrieves a RequirementOperator instance by its unique name.

        Args:
            name: The unique name of the RequirementOperator.

        Returns:
            The RequirementOperator instance with the specified name.
        """
        return self.get(name=name)


class RequirementOperator(models.Model):
    """
    A class representing a requirement operator.

    Attributes:
        name (str): The name of the requirement operator.
        description (str): A description of the requirement operator.
    """

    name = models.CharField(max_length=100, unique=True)
    description = models.TextField(blank=True, null=True)
    evaluation_function_name = models.CharField(max_length=255, blank=True, null=True)  # Added field

    objects = RequirementOperatorManager()

    if TYPE_CHECKING:
        from endoreg_db.models.requirement.requirement import Requirement

        requirements: models.QuerySet[Requirement]

    @classmethod
    def parse_instructions(cls, raw: str) -> OperatorInstructions:
        """
        Parses the raw target attributes string into structured operator instructions.

        Args:
            raw: The raw target attributes string.

        Returns:
            An OperatorInstructions instance containing parsed input targets, requirement targets, kwargs, and args.
        """
        return _parse_target_attributes(raw)

    @property
    def data_model_dict(self):
        """
        Returns the dictionary of data models used for requirement evaluation.

        This property dynamically imports and provides access to the data model dictionary relevant to requirement operators.
        """
        from .requirement_evaluation.requirement_type_parser import data_model_dict

        return data_model_dict

    def natural_key(self):
        """
        Returns a tuple containing the operator's name as its natural key.

        The natural key uniquely identifies the requirement operator for serialization and deserialization purposes.
        """
        return (self.name,)

    def __str__(self):
        """
        Returns the name of the requirement operator as its string representation.
        """
        return str(self.name)

    def get_operator_function(self):
        from endoreg_db.utils.requirement_operator_logic import 

    def evaluate(self, operator_instructions: "OperatorInstructions", requirement: "Requirement", input_obj: Any, **kwargs) -> bool:
        """ """
        requirement_links: "RequirementLinks" = requirement.links
        expected_input_models = requirement.expected_models

        input_model = type(input_obj)
        assert input_model in expected_input_models, f"Input model {input_model} not in expected models {expected_input_models}"

        # Execute Instructions
        from endoreg_db.utils.requirement_operator_logic.model_evaluators import dispatch_operator_evaluation
