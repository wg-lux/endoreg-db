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
        """Return the string representation of the requirement's name.

        Returns:
            str: The name of the requirement.
        """
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
        req_types = self.requirement_types.all()
        req_type_names = [_.name for _ in req_types]

        expected_models = [self.data_model_dict[_] for _ in req_type_names]
        return expected_models

    @property
    def links(self) -> "RequirementLinks":
        """
        Returns a RequirementsModelDict with querysets of all associated models.

        This method aggregates related models by invoking .all() on each
        many-to-many field of the requirement. The resulting RequirementsModelDict
        includes querysets for requirement types, operators, requirement sets,
        examinations, examination indications, lab values, diseases, disease
        classification choices, events, findings, finding morphology classification
        choices, finding location classification choices, and finding interventions.
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
        Returns a dictionary of data models.

        This method constructs a dictionary containing the names of the models
        and their corresponding querysets. It includes requirement types, operators,
        requirement sets, examinations, examination indications, lab values, diseases,
        disease classification choices, events, findings, finding morphology classification
        choices, finding location classification choices, and finding interventions.
        """
        from .requirement_evaluation.requirement_type_parser import data_model_dict
        return data_model_dict
    
    @property
    def active_links(self) -> Dict[str, List]:
        """
        Returns a dictionary of active links.

        This method filters the linked models to include only those that are not empty.
        Returns:
            RequirementLinks: A dictionary containing the linked models with non-empty querysets.
        """
        return self.links.active()
    
    
    def evaluate(self, *args, mode:str, **kwargs):
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
