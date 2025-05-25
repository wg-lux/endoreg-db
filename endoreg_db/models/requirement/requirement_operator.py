from django.db import models
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from endoreg_db.utils.links.requirement_link import RequirementLinks 


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


def _evaluate_models_match_any(
    requirement_links: "RequirementLinks",
    input_links: "RequirementLinks", # Changed from inputs: Dict[str, List["RequirementLinks"]]
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
    # The core logic is to check if the Requirement's own links have any commonality
    # with the combined links from all input arguments.
    return requirement_links.match_any(input_links)

class RequirementOperator(models.Model):
    """
    A class representing a requirement operator.

    Attributes:
        name (str): The name of the requirement operator.
        name_de (str): The German name of the requirement operator.
        name_en (str): The English name of the requirement operator.
        description (str): A description of the requirement operator.
    """

    name = models.CharField(max_length=100, unique=True)
    name_de = models.CharField(max_length=100, blank=True, null=True)
    name_en = models.CharField(max_length=100, blank=True, null=True)
    description = models.TextField(blank=True, null=True)

    objects = RequirementOperatorManager()

    if TYPE_CHECKING:
        from endoreg_db.models.requirement.requirement import Requirement

        requirements: models.QuerySet[Requirement]

    @property
    def operator_evaluation_models(self):
        """
        Returns a dictionary of operator evaluation models for this requirement operator.
        
        This property dynamically imports and provides access to the available operator evaluation models.
        """
        from .requirement_evaluation.operator_evaluation_models import operator_evaluation_models
        return operator_evaluation_models
    
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
    
    def evaluate(self, requirement_links: "RequirementLinks", input_links: "RequirementLinks", **kwargs) -> bool: # Changed signature
        
        """
        Evaluates the requirement operator against the provided requirement links and input links.
        
        Args:
            requirement_links: The RequirementLinks object from the Requirement model.
            input_links: The aggregated RequirementLinks object from the input arguments.
            **kwargs: Additional keyword arguments for specific operator logic.

        Returns:
            True if the condition defined by the operator is met, False otherwise.
            
        Raises:
            NotImplementedError: If the evaluation logic for the operator's name is not implemented.
        """
        # from endoreg_db.models.requirement.requirement import RequirementLinks # Already imported at TYPE_CHECKING level

        eval_func = None

        if self.name == "models_match_any":
            eval_func = _evaluate_models_match_any
        # Add other operator types here with elif self.name == "some_other_operator":
        #     eval_func = _evaluate_some_other_operator

        if eval_func:
            return eval_func(
                requirement_links=requirement_links,
                input_links=input_links,
                **kwargs
            )
        else:
            raise NotImplementedError(f"Evaluation logic for operator '{self.name}' is not implemented.")
