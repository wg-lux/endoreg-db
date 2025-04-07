from django.db import models
from typing import TYPE_CHECKING, Optional

from pydantic import BaseModel

QuerySet = models.QuerySet

if TYPE_CHECKING:
    from endoreg_db.models import (
        RequirementOperator,
        RequirementSet,
        Examination,
        ExaminationIndication,
        LabValue,
        Disease,
        DiseaseClassificationChoice,
        Event,
        Finding,
        FindingMorphologyClassificationChoice,
        FindingLocationClassificationChoice,
        FindingIntervention,
    )


class RequirementsModelDict(BaseModel):
    """
    A class representing a dictionary of models related to a requirement.

    Attributes:
        requirement_types (QuerySet[RequirementType]): A queryset of requirement types.
        operators (QuerySet[RequirementOperator]): A queryset of operators.
        requirement_sets (QuerySet[RequirementSet]): A queryset of requirement sets.
        examinations (QuerySet[Examination]): A queryset of examinations.
        examination_indications (QuerySet[ExaminationIndication]): A queryset of examination indications.
        lab_values (QuerySet[LabValue]): A queryset of lab values.
        diseases (QuerySet[Disease]): A queryset of diseases.
        disease_classification_choices (QuerySet[DiseaseClassificationChoice]): A queryset of disease classification choices.
        events (QuerySet[Event]): A queryset of events.
        findings (QuerySet[Finding]): A queryset of findings.
        finding_morphology_classification_choices (QuerySet[FindingMorphologyClassificationChoice]): A queryset of finding morphology classification choices.
        finding_location_classification_choices (QuerySet[FindingLocationClassificationChoice]): A queryset of finding location classification choices.
        finding_interventions (QuerySet[FindingIntervention]): A queryset of finding interventions.
    """

    model_config = {"arbitrary_types_allowed": True}
    requirement_types: Optional[QuerySet["RequirementType"]] = None
    operators: Optional[QuerySet["RequirementOperator"]] = None
    requirement_sets: Optional[QuerySet["RequirementSet"]] = None
    examinations: Optional[QuerySet["Examination"]] = None
    examination_indications: Optional[QuerySet["ExaminationIndication"]] = None
    lab_values: Optional[QuerySet["LabValue"]] = None
    diseases: Optional[QuerySet["Disease"]] = None
    disease_classification_choices: Optional[
        QuerySet["DiseaseClassificationChoice"]
    ] = None
    events: Optional[QuerySet["Event"]] = None
    findings: Optional[QuerySet["Finding"]] = None
    finding_morphology_classification_choices: Optional[
        QuerySet["FindingMorphologyClassificationChoice"]
    ] = None
    finding_location_classification_choices: Optional[
        QuerySet["FindingLocationClassificationChoice"]
    ] = None
    finding_interventions: Optional[QuerySet["FindingIntervention"]] = None


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

    def get_models_dict(self) -> RequirementsModelDict:
        """
        Returns a RequirementsModelDict with querysets of all associated models.
        
        This method aggregates related models by invoking .all() on each
        many-to-many field of the requirement. The resulting RequirementsModelDict
        includes querysets for requirement types, operators, requirement sets,
        examinations, examination indications, lab values, diseases, disease
        classification choices, events, findings, finding morphology classification
        choices, finding location classification choices, and finding interventions.
        """
        models_dict = RequirementsModelDict(
            requirement_types=self.requirement_types.all(),
            operators=self.operators.all(),
            requirement_sets=self.requirement_sets.all(),
            examinations=self.examinations.all(),
            examination_indications=self.examination_indications.all(),
            lab_values=self.lab_values.all(),
            diseases=self.diseases.all(),
            disease_classification_choices=self.disease_classification_choices.all(),
            events=self.events.all(),
            findings=self.findings.all(),
            finding_morphology_classification_choices=self.finding_morphology_classification_choices.all(),
            finding_location_classification_choices=self.finding_location_classification_choices.all(),
            finding_interventions=self.finding_interventions.all(),
        )
        return models_dict
