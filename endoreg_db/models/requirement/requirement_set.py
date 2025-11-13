from typing import TYPE_CHECKING, List

from django.db import models

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

    reqset_exam_links = models.ManyToManyField(
        "ExaminationRequirementSet",
        related_name="requirement_set",
        blank=True,
    )

    tags = models.ManyToManyField(
        "Tag",
        related_name="requirement_sets",
        blank=True,
    )

    objects = RequirementSetManager()

    if TYPE_CHECKING:
        from typing import Optional, cast

        from endoreg_db.models import ExaminationRequirementSet, InformationSource, Requirement, Tag

        tags = cast(models.manager.RelatedManager["Tag"], tags)
        requirements = cast(models.manager.RelatedManager["Requirement"], requirements)
        links_to_sets = cast(models.manager.RelatedManager["RequirementSet"], links_to_sets)
        reqset_exam_links = cast(models.manager.RelatedManager["ExaminationRequirementSet"], reqset_exam_links)
        information_sources = cast(models.manager.RelatedManager["InformationSource"], information_sources)
        requirement_set_type: models.ForeignKey["RequirementSetType | None"]

        @property
        def links_from_sets(self) -> "models.manager.RelatedManager[RequirementSet]": ...

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

        Intelligently selects the appropriate input data for each requirement based on its expected model types.
        For example, if a requirement expects PatientFinding but receives a PatientExamination,
        it will use the examination's patient_findings instead.

        Args:
            input_object: The object to be evaluated by each requirement.
            mode: Optional evaluation mode passed to each requirement (default is "loose").

        Returns:
            A list of boolean values indicating whether each requirement is satisfied.
        """
        results = []
        for requirement in self.requirements.all():
            # Get the appropriate input for this specific requirement
            evaluation_input = self._get_evaluation_input_for_requirement(requirement, input_object)
            result = requirement.evaluate(evaluation_input, mode=mode)
            results.append(result)
        return results

    def _get_evaluation_input_for_requirement(self, requirement, input_object):
        """
        Determines the appropriate input object for evaluating a specific requirement.

        Args:
            requirement: The requirement to be evaluated
            input_object: The original input object

        Returns:
            The most appropriate input object for the requirement evaluation
        """
        expected_models = requirement.expected_models

        # If the input object is already one of the expected models, use it directly
        for expected_model in expected_models:
            if isinstance(input_object, expected_model):
                return input_object

        # Import here to avoid circular imports
        from endoreg_db.models.medical.patient.patient_examination import PatientExamination
        from endoreg_db.models.medical.patient.patient_finding import PatientFinding

        # Handle PatientExamination -> PatientFinding conversion
        if isinstance(input_object, PatientExamination):
            # If the requirement expects PatientFinding instances, scope queryset to relevant findings
            if PatientFinding in expected_models:
                findings_qs = input_object.patient_findings.all()
                required_findings = list(requirement.findings.all())
                if required_findings:
                    findings_qs = findings_qs.filter(finding__in=required_findings)
                return findings_qs

        # Handle other model conversions as needed in the future
        # For now, return the original input object as fallback
        return input_object

    def evaluate_requirement_sets(self, input_object) -> List[bool]:
        """
        Evaluates all linked requirement sets against the provided input object.

        Returns:
            A list of boolean values indicating whether each linked requirement set is satisfied.
        """
        results = []
        linked_sets = self.all_linked_sets
        if not linked_sets:
            return results
        for linked_set in linked_sets:
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

    @property
    def all_linked_sets(self):
        """
        Returns all linked requirement sets, including those linked to the current set and those linked to any of its linked sets.

        Uses recursive traversal with cycle detection to safely handle circular relationships.
        Eliminates duplicates by tracking visited sets.

        Returns:
            List[RequirementSet]: A list of all linked requirement sets.
        """
        visited = set()
        result: List["RequirementSet"] = []

        def _collect_linked_sets(requirement_set: "RequirementSet"):
            """
            Recursively collect linked sets while avoiding cycles and duplicates.

            Args:
                requirement_set: The RequirementSet to process
            """
            # Use the primary key to track visited sets (avoids issues with object comparison)
            if requirement_set.pk in visited:
                return

            visited.add(requirement_set.pk)

            # Process all directly linked sets
            for linked_set in requirement_set.links_to_sets.all():
                if linked_set.pk not in visited:
                    result.append(linked_set)
                    # Recursively process the linked set's links
                    _collect_linked_sets(linked_set)

        # Start the recursive collection from this instance
        _collect_linked_sets(self)

        return result
