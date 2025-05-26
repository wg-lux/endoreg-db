from django.db import models
from typing import TYPE_CHECKING, Dict, List, Union
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
    # from endoreg_db.utils.links.requirement_link import RequirementLinks # Already imported above


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
        """Returns the name of the requirement as its string representation."""
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
        Returns a list of model classes expected as input based on the requirement's linked types.
        
        The returned list corresponds to the model classes mapped from the names of all associated requirement types.
        """
        req_types = self.requirement_types.all()
        req_type_names = [_.name for _ in req_types]

        expected_models = [self.data_model_dict[_] for _ in req_type_names]
        return expected_models

    @property
    def links(self) -> "RequirementLinks":
        """
        Aggregates and returns all related model instances as a RequirementLinks object.
        
        The returned RequirementLinks contains lists of all non-null related objects from the requirement's many-to-many fields, providing a structured view of its associations.
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
        Provides a mapping from requirement type names to their corresponding model classes.
        
        Returns:
            A dictionary where keys are requirement type names and values are model classes used for requirement evaluation.
        """
        from .requirement_evaluation.requirement_type_parser import data_model_dict
        return data_model_dict
    
    @property
    def active_links(self) -> Dict[str, List]:
        """Returns a dictionary of linked models containing only non-empty entries.
        
        The returned dictionary includes only those related model lists that have at least one linked instance.
        """
        return self.links.active()
    
    
    def evaluate(self, *args, mode:str, **kwargs):
        """
        Evaluates the requirement against provided input models using linked operators.
        
        Args:
            *args: Instances of expected model classes (e.g., PatientExamination, ExaminationIndication) to be evaluated.
                   Each input instance must have a `.links` property returning a RequirementLinks object.
            mode: Evaluation mode, either "strict" (all operators must pass) or "loose" (any operator may pass).
            **kwargs: Additional keyword arguments passed to operator evaluations.
        
        Returns:
            True if the requirement is satisfied according to the specified mode and linked operators; otherwise, False.
        
        Raises:
            ValueError: If an invalid mode is provided.
            TypeError: If an input is not an instance of an expected model class or its `.links` attribute is not a RequirementLinks instance.
        """
        if mode not in ["strict", "loose"]:
            raise ValueError(f"Invalid mode: {mode}. Use 'strict' or 'loose'.")

        evaluate_result_list_func = all if mode == "strict" else any

        requirement_req_links = self.links
        expected_models_tuple = tuple(self.expected_models) # For faster type checking

        # Aggregate RequirementLinks from all input arguments
        aggregated_input_links_data = {}
        processed_inputs_count = 0

        for _input in args:
            # Check if the input is an instance of any of the expected model types
            if not isinstance(_input, expected_models_tuple):
                # Allow QuerySets of expected models
                if isinstance(_input, models.QuerySet) and issubclass(_input.model, expected_models_tuple):
                    # Process each item in the queryset
                    if not _input.exists(): # Skip empty querysets
                        continue
                    for item in _input:
                        if not hasattr(item, 'links') or not isinstance(item.links, RequirementLinks):
                            raise TypeError(
                                f"Item {item} of type {type(item)} in QuerySet does not have a valid .links attribute of type RequirementLinks."
                            )
                        active_item_links = item.links.active()
                        for link_key, link_list in active_item_links.items():
                            if link_key not in aggregated_input_links_data:
                                aggregated_input_links_data[link_key] = []
                            aggregated_input_links_data[link_key].extend(link_list)
                        processed_inputs_count +=1
                    continue # Move to the next arg after processing queryset
                else:
                    raise TypeError(
                        f"Input type {type(_input)} is not among expected models: {self.expected_models} "
                        f"nor a QuerySet of expected models."
                    )

            # Process single model instance
            if not hasattr(_input, 'links') or not isinstance(_input.links, RequirementLinks):
                raise TypeError(
                    f"Input {_input} of type {type(_input)} does not have a valid .links attribute of type RequirementLinks."
                )
            
            active_input_links = _input.links.active() # Get dict of non-empty lists
            for link_key, link_list in active_input_links.items():
                if link_key not in aggregated_input_links_data:
                    aggregated_input_links_data[link_key] = []
                aggregated_input_links_data[link_key].extend(link_list)
            processed_inputs_count += 1

        if not processed_inputs_count and args: # If args were provided but none were processable (e.g. all empty querysets)
             # This situation implies no relevant data was provided for evaluation against the requirement.
             # Depending on operator logic (e.g., "requires at least one matching item"), this might lead to False.
             # For "models_match_any", an empty input_links will likely result in False if requirement_req_links is not empty.
             pass


        # Deduplicate items within each list after aggregation
        for key in aggregated_input_links_data:
            try:
                # Using dict.fromkeys to preserve order and remove duplicates for hashable items
                aggregated_input_links_data[key] = list(dict.fromkeys(aggregated_input_links_data[key]))
            except TypeError:
                # Fallback for non-hashable items (though Django models are hashable)
                temp_list = []
                for item in aggregated_input_links_data[key]:
                    if item not in temp_list:
                        temp_list.append(item)
                aggregated_input_links_data[key] = temp_list
        
        final_input_links = RequirementLinks(**aggregated_input_links_data)
        
        operators = self.operators.all()
        if not operators.exists(): # If a requirement has no operators, its evaluation is ambiguous.
            # Consider if this should be True, False, or an error.
            # For now, if no operators, and mode is strict, it's vacuously true. If loose, vacuously false.
            # However, typically a requirement implies some condition.
            # Let's assume if no operators, it cannot be satisfied unless it also has no specific links.
            # This behavior might need further refinement based on business logic.
            if not requirement_req_links.active(): # No conditions in requirement
                 return True # Vacuously true if requirement itself is empty
            return False # Cannot be satisfied if requirement has conditions but no operators to check them


        operator_results = []
        for operator in operators:
            # Prepare kwargs for the operator, including the current Requirement instance
            op_kwargs = kwargs.copy() # Start with kwargs passed to Requirement.evaluate
            op_kwargs['requirement'] = self # Add the Requirement instance itself
            operator_results.append(operator.evaluate(
                requirement_links=requirement_req_links,
                input_links=final_input_links,
                **op_kwargs
            ))

        is_valid = evaluate_result_list_func(operator_results)

        return is_valid
