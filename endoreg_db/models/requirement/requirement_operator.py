from django.db import models
from typing import TYPE_CHECKING, Any, Dict, List

if TYPE_CHECKING:
    from endoreg_db.utils.links.requirement_link import RequirementLinks 


class RequirementOperatorManager(models.Manager):
    def get_by_natural_key(self, name):
        """
        Retrieve a RequirementOperator instance by its natural key.
        
        Args:
            name (str): The unique name representing the natural key.
        
        Returns:
            RequirementOperator: The model instance matching the provided name.
        """
        return self.get(name=name)


def _evaluate_models_match_any(
    requirement_links: "RequirementLinks",
    inputs: Dict[str, List["RequirementLinks"]],
    **kwargs
) -> bool:
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
        Retrieve the operator evaluation models associated with this requirement operator.
        
        This property dynamically imports the operator evaluation models module and returns
        the dictionary of models for the current operator.
        
        Returns:
            dict: A dictionary of operator evaluation models.
        """
        from .requirement_evaluation.operator_evaluation_models import operator_evaluation_models
        return operator_evaluation_models
    
    @property
    def data_model_dict(self):
        """
        Retrieve the data model dictionary associated with this requirement operator.
        
        This property dynamically imports the data model dictionary module and returns
        the dictionary of models for the current operator.
        
        Returns:
            dict: A dictionary of data models.
        """
        from .requirement_evaluation.requirement_type_parser import data_model_dict
        return data_model_dict

    def natural_key(self):
        """
        Return the natural key for the requirement operator.
        
        This method returns a tuple containing the operator's name, which serves as its
        natural key for serialization and unique identification within the system.
        """
        return (self.name,)

    def __str__(self):
        """
        Return the string representation of the requirement operator.
        
        Returns:
            str: The name attribute of the operator.
        """
        return str(self.name)
    
    def evaluate(self, requirement:"Requirement", inputs:Dict[str, List["RequirementLinks"]], **kwargs) -> bool:
        
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
