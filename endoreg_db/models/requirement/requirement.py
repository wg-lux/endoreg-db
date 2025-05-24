from django.db import models
from typing import TYPE_CHECKING, Any, Dict, Optional, List, Union
from endoreg_db.utils.links.requirement_link import RequirementLinks


QuerySet = models.QuerySet

if TYPE_CHECKING:
    from endoreg_db.models import (
        Disease,
        DiseaseClassificationChoice,
        Event,
        EventClassification,
        EventClassificationChoice,
        Examination,
        ExaminationIndication,
        Finding,
        FindingIntervention,
        FindingLocationClassification,
        FindingLocationClassificationChoice,
        FindingMorphologyClassification,
        FindingMorphologyClassificationChoice,
        FindingMorphologyClassificationType,
        LabValue,
        PatientDisease,
        PatientEvent,
        PatientExamination,
        PatientFinding,
        PatientFindingIntervention,
        PatientFindingLocation,
        PatientFindingMorphology,
        PatientLabValue,
        RequirementOperator,
        RequirementSet
    )
    from endoreg_db.utils.links.requirement_link import RequirementLinks


class RequirementTypeManager(models.Manager):
    def get_by_natural_key(self, name):
        """
        Retrieve a model instance using its natural key.

        Queries the database for an instance with a matching name, serving as the natural key.

        Args:
            name: The natural key identifying the model instance.

        Returns:
            The model instance matching the provided natural key.
        """
        return self.get(name=name)


class RequirementType(models.Model):
    """
    A class representing a type of requirement.

    Attributes:
        name (str): The name of the requirement type.
        name_de (str): The German name of the requirement type.
        name_en (str): The English name of the requirement type.
    """

    name = models.CharField(max_length=100, unique=True)
    name_de = models.CharField(max_length=100, blank=True, null=True)
    name_en = models.CharField(max_length=100, blank=True, null=True)
    description = models.TextField(blank=True, null=True)

    objects = RequirementTypeManager()

    def natural_key(self):
        """
        Return the natural key for the instance as a tuple containing its name.

        This tuple enables the use of natural key lookups for serialization and deserialization.
        """
        return (self.name,)

    def __str__(self):
        """Return the string representation of the requirement type's name.

        Returns:
            str: The name of the requirement type.
        """
        return str(self.name)


class RequirementManager(models.Manager):
    def get_by_natural_key(self, name):
        """
        Retrieve an instance using its natural key.

        Args:
            name: The natural key used to look up the instance.

        Returns:
            The object whose 'name' field matches the given key.
        """
        return self.get(name=name)


class Requirement(models.Model):
    """
    A class representing a requirement.

    Attributes:
        name (str): The name of the requirement.
        name_de (str): The German name of the requirement.
        name_en (str): The English name of the requirement.
        description (str): A description of the requirement.
    """

    name = models.CharField(max_length=100, unique=True)
    name_de = models.CharField(max_length=100, blank=True, null=True)
    name_en = models.CharField(max_length=100, blank=True, null=True)
    description = models.TextField(blank=True, null=True)
    numeric_value = models.FloatField(
        blank=True,
        null=True,
        help_text="Numeric value for the requirement. If not set, the requirement is not used in calculations.",
    )

    numeric_value_min = models.FloatField(
        blank=True,
        null=True,
        help_text="Minimum numeric value for the requirement. If not set, the requirement is not used in calculations.",
    )
    numeric_value_max = models.FloatField(
        blank=True,
        null=True,
        help_text="Maximum numeric value for the requirement. If not set, the requirement is not used in calculations.",
    )

    string_value = models.CharField(
        max_length=100,
        blank=True,
        null=True,
        help_text="String value for the requirement. If not set, the requirement is not used in calculations.",
    )

    string_values = models.TextField(
        blank=True,
        null=True,
        help_text=" ','-separated list of string values for the requirement.If not set, the requirement is not used in calculations.",
    )

    objects = RequirementManager()

    requirement_types = models.ManyToManyField(
        "RequirementType",
        blank=True,
        related_name="linked_requirements",
    )

    operators = models.ManyToManyField(
        "RequirementOperator",
        blank=True,
        related_name="required_in",
    )

    unit = models.ForeignKey(
        "Unit",
        on_delete=models.CASCADE,
        related_name="required_in",
        blank=True,
        null=True,
    )

    examinations = models.ManyToManyField(
        "Examination",
        blank=True,
        related_name="required_in",
    )

    examination_indications = models.ManyToManyField(
        "ExaminationIndication",
        blank=True,
        related_name="required_in",
    )

    diseases = models.ManyToManyField(
        "Disease",
        blank=True,
        related_name="required_in",
    )

    disease_classification_choices = models.ManyToManyField(
        "DiseaseClassificationChoice",
        blank=True,
        related_name="required_in",
    )

    events = models.ManyToManyField(
        "Event",
        blank=True,
        related_name="required_in",
    )

    lab_values = models.ManyToManyField(
        "LabValue",
        blank=True,
        related_name="required_in",
    )

    findings = models.ManyToManyField(
        "Finding",
        blank=True,
        related_name="required_in",
    )

    finding_morphology_classification_choices = models.ManyToManyField(
        "FindingMorphologyClassificationChoice",
        blank=True,
        related_name="required_in",
    )

    finding_location_classification_choices = models.ManyToManyField(
        "FindingLocationClassificationChoice",
        blank=True,
        related_name="required_in",
    )

    finding_interventions = models.ManyToManyField(
        "FindingIntervention",
        blank=True,
        related_name="required_in",
    )

    if TYPE_CHECKING:
        requirement_types: models.QuerySet[RequirementType]
        operators: models.QuerySet[RequirementOperator]
        requirement_sets: models.QuerySet[RequirementSet]
        examinations: models.QuerySet[Examination]
        examination_indications: models.QuerySet[ExaminationIndication]
        lab_values: models.QuerySet[LabValue]
        diseases: models.QuerySet[Disease]
        disease_classification_choices: models.QuerySet[DiseaseClassificationChoice]
        events: models.QuerySet[Event]
        findings: models.QuerySet[Finding]
        finding_morphology_classification_choices: models.QuerySet[
            FindingMorphologyClassificationChoice
        ]
        finding_location_classification_choices: models.QuerySet[
            FindingLocationClassificationChoice
        ]
        finding_interventions: models.QuerySet[FindingIntervention]

    def natural_key(self):
        """
        Return a tuple containing the natural key of the instance.

        The tuple, comprised solely of the instance's name, serves as an alternative unique identifier for serialization.
        """
        return (self.name,)

    def __str__(self):
        Returns the name of the requirement as its string representation.
        return str(self.name)

    @property
    def expected_models(self) -> List[Union[
        "Disease",
        "DiseaseClassificationChoice",
        "Event",
        "EventClassification",
        "EventClassificationChoice",
        "Examination",
        "ExaminationIndication",
        "Finding",
        "FindingIntervention",
        "FindingLocationClassification",
        "FindingLocationClassificationChoice",
        "FindingMorphologyClassification",
        "FindingMorphologyClassificationChoice",
        "FindingMorphologyClassificationType",
        "LabValue",
        "PatientDisease",
        "PatientEvent",
        "PatientExamination",
        "PatientFinding",
        "PatientFindingIntervention",
        "PatientFindingLocation",
        "PatientFindingMorphology",
        "PatientLabValue",
    ]]:
        """
        Returns a list of model classes expected for this requirement based on its linked requirement types.
        
        The returned list contains model classes corresponding to each associated requirement type, as defined in the data model mapping.
        """
        req_types = self.requirement_types.all()
        req_type_names = [_.name for _ in req_types]

        expected_models = [self.data_model_dict[_] for _ in req_type_names]
        return expected_models

    @property
    def links(self) -> "RequirementLinks":
        """
        Aggregates all related model instances into a RequirementLinks object.
        
        Returns:
            A RequirementLinks instance containing lists of all non-null related objects
            from the requirement's many-to-many fields, such as examinations, lab values,
            diseases, events, findings, and classification choices.
        """
        models_dict = RequirementLinks(
            # requirement_types=self.requirement_types.all(),
            # operators=self.operators.all(),
            requirement_sets=[_ for _ in self.requirement_sets.all() if _ is not None],
            examinations=[_ for _ in self.examinations.all() if _ is not None],
            examination_indications=[_ for _ in self.examination_indications.all() if _ is not None],
            lab_values=[_ for _ in self.lab_values.all() if _ is not None],
            diseases=[_ for _ in self.diseases.all() if _ is not None],
            disease_classification_choices=[_ for _ in self.disease_classification_choices.all() if _ is not None],
            events=[_ for _ in self.events.all() if _ is not None],
            findings=[_ for _ in self.findings.all() if _ is not None],
            finding_morphology_classification_choices=[
                _ for _ in self.finding_morphology_classification_choices.all() if _ is not None
            ],
            finding_location_classification_choices=[
                _ for _ in self.finding_location_classification_choices.all() if _ is not None
            ],
            finding_interventions=[_ for _ in self.finding_interventions.all() if _ is not None],
        )
        return models_dict
    
    @property
    def data_model_dict(self) -> dict:
        """
        Returns a dictionary mapping requirement type names to their corresponding model classes.
        
        The dictionary is imported from the requirement type parser and is used to determine which models are expected for each requirement type.
        """
        from .requirement_evaluation.requirement_type_parser import data_model_dict
        return data_model_dict
    
    @property
    def active_links(self) -> Dict[str, List]:
        Returns a dictionary of related model lists that are non-empty.
        
        The returned dictionary includes only those linked models for which there is at least one related instance.
        return self.links.active()
    
    
    def evaluate(self, *args, mode:str, **kwargs):
        """
        Evaluates whether the requirement is satisfied by the provided input objects using linked operators.
        
        Args:
            *args: Input objects whose types must match the expected models for this requirement.
            mode: Evaluation mode; "strict" requires all operators to return True, "loose" requires at least one.
        
        Returns:
            True if the requirement is satisfied according to the specified mode and linked operators; otherwise, False.
        
        Raises:
            ValueError: If an invalid mode is provided.
            AssertionError: If input objects are not of the expected types or do not provide valid RequirementLinks.
        """
        if mode not in ["strict", "loose"]:
            raise ValueError(f"Invalid mode: {mode}. Use 'strict' or 'loose'.")
        if mode == "strict":
            evaluate_result_list_func = all

        elif mode == "loose":
            evaluate_result_list_func = any
        
        else:
            raise ValueError(f"Invalid mode: {mode}. Use 'strict' or 'loose'.")

        requirement_req_links = self.links

        expected_models = self.expected_models

        inputs = [_ for _ in args]
        input_req_links = {_model:[] for _model in expected_models}

        for _input in inputs:
            input_type = type(_input)
            assert input_type in expected_models, f"Expected {expected_models}, got {input_type}"

            _input_links: RequirementLinks = _input.links
            assert isinstance(_input_links, RequirementLinks), f"Expected RequirementLinks, got {type(_input_links)}"
            input_req_links[input_type].append(_input_links)

        input_req_links_obj = RequirementLinks(
            **input_req_links
        )

        operators = self.operators.all()

        operator_results = []
        for operator in operators:
            #TODO
            operator_results.append(operator.evaluate(
                requirement_links=requirement_req_links,
                input_links=input_req_links_obj,
                **kwargs
            ))

        is_valid = evaluate_result_list_func(
            operator_results
        )

        return is_valid
