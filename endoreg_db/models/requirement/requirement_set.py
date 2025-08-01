from django.db import models
from typing import TYPE_CHECKING, List


REQUIREMENT_SET_TYPE_FUNCTION_LOOKUP = {
    "all": all,
    "any": any,
    "none": lambda x: not any(x),  # Custom function for 'none'
    "exactly_1": lambda x: sum(1 for item in x if item) == 1,
    "at_least_1": lambda x: sum(1 for item in x if item) >= 1,  # Equivalent to any(x)
    "at_most_1": lambda x: sum(1 for item in x if item) <= 1,
}

class RequirementSetTypeManager(models.Manager):
    """
    Manager for RequirementSetType with custom query methods.
    """

    def get_by_natural_key(self, name: str) -> "RequirementSetType":
        """
        Retrieves a RequirementSetType instance using its natural key.
        
        Args:
            name (str): The unique name that serves as the natural key.
        
        Returns:
            RequirementSetType: The matching RequirementSetType instance.
        """
        return self.get(name=name)


class RequirementSetType(models.Model):
    """
    A class representing a type of requirement set.

    Attributes:
        name (str): The name of the requirement set type.
    """

    name = models.CharField(max_length=100, unique=True)
    description = models.TextField(blank=True, null=True)

    objects = RequirementSetTypeManager()

    if TYPE_CHECKING:
        from endoreg_db.models.requirement import RequirementType

        requirement_types: models.QuerySet[RequirementType]

    def natural_key(self):
        """
        Return the natural key tuple for the instance.
        
        Returns:
            tuple: A one-element tuple containing the instance's name, used as its natural key.
        """
        return (self.name,)


class RequirementSetManager(models.Manager):
    def get_by_natural_key(self, name):
        """
        Retrieves a model instance by its natural key.
        
        Args:
            name: The natural key value, typically corresponding to the model's unique name.
        
        Returns:
            The model instance whose name matches the provided natural key.
        """
        return self.get(name=name)


class RequirementSet(models.Model):
    """
    A class representing a set of requirements.

    Attributes:
        name (str): The name of the requirement set.
        description (str): A description of the requirement set.
    """

    name = models.CharField(max_length=100, unique=True)
    description = models.TextField(blank=True, null=True)
    requirements = models.ManyToManyField(
        "Requirement",
        blank=True,
        related_name="requirement_sets",
    )
    links_to_sets = models.ManyToManyField(
        "RequirementSet",
        blank=True,
        related_name="links_from_sets",
    )
    requirement_set_type = models.ForeignKey(
        "RequirementSetType",
        on_delete=models.CASCADE,
        related_name="requirement_sets",
        blank=True,
        null=True,
    )
    information_sources = models.ManyToManyField(
        "InformationSource",
        related_name="requirement_sets",
        blank=True,
    )
    objects = RequirementSetManager()

    if TYPE_CHECKING:
        from endoreg_db.models import Requirement, InformationSource

        requirements: models.QuerySet[Requirement]
        information_source: InformationSource
        requirement_set_type: RequirementSetType
        linked_sets: models.QuerySet["RequirementSet"]

    def natural_key(self):
        """Return the natural key as a tuple containing the instance's name."""
        return (self.name,)

    def __str__(self):
        """
        Returns the name of the requirement set as its string representation.
        """
        return str(self.name)
    
    def evaluate_requirements(self, input_object, mode="loose") -> List[bool]:
        """
        Evaluates all requirements in the set against the provided input object.
        
        Args:
            input_object: The object to be evaluated by each requirement.
            mode: Optional evaluation mode passed to each requirement (default is "loose").
        
        Returns:
            A list of boolean values indicating whether each requirement is satisfied.
        """
        results = []
        for requirement in self.requirements.all():
            result = requirement.evaluate(input_object, mode=mode)
            results.append(result)
        return results

    def evaluate_requirement_sets(self, input_object) -> List[bool]:
        """
        Evaluates all linked requirement sets against the provided input object.
        
        Returns:
            A list of boolean values indicating whether each linked requirement set is satisfied.
        """
        results = []
        for linked_set in self.links_to_sets.all():
            result = linked_set.evaluate(input_object)
            results.append(result)
        return results
    
    @property
    def eval_function(self):
        """
        Returns the evaluation function associated with this requirement set's type.
        
        If the requirement set type is defined and matches a known type, returns the corresponding function from REQUIREMENT_SET_TYPE_FUNCTION_LOOKUP. Returns None if no matching function is found.
        """
        if self.requirement_set_type and self.requirement_set_type.name in REQUIREMENT_SET_TYPE_FUNCTION_LOOKUP:
            return REQUIREMENT_SET_TYPE_FUNCTION_LOOKUP[self.requirement_set_type.name]
        return None

    def evaluate(self, input_object):
        """
        Evaluates whether the input object satisfies this requirement set.
        
        Combines the evaluation results of all direct requirements and linked requirement sets, then applies the set's evaluation function (such as all, any, none, etc.) to determine if the input object meets the overall criteria.
        
        Args:
            input_object: The object to be evaluated against the requirements and linked sets.
        
        Returns:
            True if the input object satisfies the requirement set according to its evaluation logic; otherwise, False.
        """
        evaluate_r_results = self.evaluate_requirements(input_object)
        evaluate_rs_results = self.evaluate_requirement_sets(input_object)

        results = evaluate_r_results + evaluate_rs_results

        eval_result = self.eval_function(results) if self.eval_function else all(results)

        return eval_result

