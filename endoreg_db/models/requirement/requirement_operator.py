from django.db import models
from typing import TYPE_CHECKING, Any, Dict, List

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
    inputs: Dict[str, List["RequirementLinks"]],
    **kwargs
) -> bool:
    """
    Checks if any set of requirement links in the provided inputs matches the given requirement links.
    
    Iterates over each model's requirement links in the inputs and returns True if any set matches according to the `match_any` method. Returns False if no matches are found.
    
    Args:
        requirement_links: The reference set of requirement links to compare against.
        inputs: A dictionary mapping model names to lists of requirement links.
    
    Returns:
        True if any input set of requirement links matches; otherwise, False.
    """
    for model_name, req_links in inputs.items():
        is_true = requirement_links.match_any(req_links)
        if is_true:
            return True
    return False
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
    
    def evaluate(self, requirement:"Requirement", inputs:Dict[str, List["RequirementLinks"]], **kwargs) -> bool:
        
        """
        Evaluates the requirement operator against the provided requirement and input data.
        
        This method is intended to execute the logic associated with the operator's name, using the given requirement and a dictionary of input model data. The evaluation logic is not yet implemented.
        """
        from endoreg_db.models.requirement.requirement import RequirementLinks

        is_true = False

        eval_func = None

        if self.name == "models_match_any":
            eval_func = self.evaluate

    # def get_function(self):
    #     """
    #     Retrieve the function associated with the requirement operator.
        
    #     This method looks up the operator name in the supported operators mapping
    #     and returns the corresponding operator function used for evaluating requirements.
    #     If the operator name is not recognized, the behavior is undefined.
        
    #     Returns:
    #         function: The operator function associated with the operator name.
    #     """
    #     from endoreg_db.models.requirement.requirement_evaluation import get_operator_function

    #     return get_operator_function(self.name)
