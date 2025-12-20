import logging
from subprocess import run
from typing import TYPE_CHECKING, Dict, List, Tuple, Union, cast

from django.db import models

from endoreg_db.utils.links.requirement_link import RequirementLinks

logger = logging.getLogger(__name__)


QuerySet = models.QuerySet

if TYPE_CHECKING:
    from endoreg_db.models import (  # RequirementSet,
        Disease,
        DiseaseClassificationChoice,
        Event,
        EventClassification,
        EventClassificationChoice,
        Examination,
        ExaminationIndication,
        Finding,
        FindingClassification,
        FindingClassificationChoice,
        FindingClassificationType,
        FindingIntervention,
        Gender,
        LabValue,
        Medication,
        MedicationIndication,
        MedicationIntakeTime,  # Added MedicationIntakeTime
        MedicationSchedule,
        PatientDisease,
        PatientEvent,
        PatientExamination,
        PatientFinding,
        PatientFindingClassification,
        PatientFindingIntervention,
        PatientLabValue,
        PatientMedicationSchedule,  # Added PatientMedicationSchedule
        RequirementOperator,
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
    """

    name = models.CharField(max_length=100, unique=True)
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
        description (str): A description of the requirement.
    """

    name = models.CharField(max_length=100, unique=True)
    description = models.TextField(blank=True, null=True)

    operator_instructions = models.TextField(
        help_text="semicolon-separated list of target attributes for the requirement",
    )

    @property
    def operator_instructions_parsed(self):
        from endoreg_db.models.requirement.requirement_operator import (
            RequirementOperator,
        )

        instructions = RequirementOperator.parse_instructions(
            self.operator_instructions
        )
        return instructions

    numeric_value = models.FloatField(
        blank=True,
        null=True,
        help_text="Numeric value for the requirement. ons.",
    )
    numeric_value_min = models.FloatField(
        blank=True,
        null=True,
        help_text="Minimum numeric value for the requirement. ons.",
    )
    numeric_value_max = models.FloatField(
        blank=True,
        null=True,
        help_text="Maximum numeric value for the requirement. ons.",
    )
    string_value = models.CharField(
        max_length=100,
        blank=True,
        null=True,
        help_text="String value for the requirement. ons.",
    )
    string_values = models.TextField(
        blank=True,
        null=True,
        help_text=" ','-separated list of string values for the requirement.ons.",
    )
    objects = RequirementManager()

    requirement_types = models.ManyToManyField(
        "RequirementType",
        blank=True,
        related_name="linked_requirements",
    )

    operator = models.ForeignKey(
        "RequirementOperator",
        on_delete=models.CASCADE,
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

    finding_classifications = models.ManyToManyField(
        "FindingClassification",
        blank=True,
        related_name="required_in",
    )

    finding_classification_choices = models.ManyToManyField(
        "FindingClassificationChoice",
        blank=True,
        related_name="required_in",
    )

    finding_interventions = models.ManyToManyField(
        "FindingIntervention",
        blank=True,
        related_name="required_in",
    )

    medications = models.ManyToManyField(
        "Medication",
        blank=True,
        related_name="required_in",
    )

    medication_indications = models.ManyToManyField(
        "MedicationIndication",
        blank=True,
        related_name="required_in",
    )

    medication_intake_times = models.ManyToManyField(
        "MedicationIntakeTime",
        blank=True,
        related_name="required_in",
    )

    medication_schedules = models.ManyToManyField(
        "MedicationSchedule",
        blank=True,
        related_name="required_in",
    )

    genders = models.ManyToManyField(
        "Gender",
        blank=True,
        related_name="required_in",
    )

    if TYPE_CHECKING:
        requirement_types = cast(
            models.manager.RelatedManager["RequirementType"], requirement_types
        )
        operator = models.ForeignKey["RequirementOperator"]
        # requirement_sets = cast(models.manager.RelatedManager["RequirementSet"], requirement_sets)
        examinations = cast(models.manager.RelatedManager["Examination"], examinations)
        examination_indications = cast(
            models.manager.RelatedManager["ExaminationIndication"],
            examination_indications,
        )
        lab_values = cast(models.manager.RelatedManager["LabValue"], lab_values)
        diseases = cast(models.manager.RelatedManager["Disease"], diseases)
        disease_classification_choices = cast(
            models.manager.RelatedManager["DiseaseClassificationChoice"],
            disease_classification_choices,
        )
        events = cast(models.manager.RelatedManager["Event"], events)
        findings = cast(models.manager.RelatedManager["Finding"], findings)
        finding_classifications = cast(
            models.manager.RelatedManager["FindingClassification"],
            finding_classifications,
        )
        finding_classification_choices = cast(
            models.manager.RelatedManager["FindingClassificationChoice"],
            finding_classification_choices,
        )
        finding_interventions = cast(
            models.manager.RelatedManager["FindingIntervention"], finding_interventions
        )
        medications = cast(models.manager.RelatedManager["Medication"], medications)
        medication_indications = cast(
            models.manager.RelatedManager["MedicationIndication"],
            medication_indications,
        )
        medication_intake_times = cast(
            models.manager.RelatedManager["MedicationIntakeTime"],
            medication_intake_times,
        )
        medication_schedules = cast(
            models.manager.RelatedManager["MedicationSchedule"], medication_schedules
        )
        genders = cast(models.manager.RelatedManager["Gender"], genders)

    def natural_key(self):
        """
        Returns a tuple containing the instance's name as its natural key.

        This tuple provides a unique identifier for serialization purposes.
        """
        return (self.name,)

    def __str__(self):
        """Returns the name of the requirement as its string representation."""
        return str(self.name)

    @property
    def expected_models(
        self,
    ) -> List[
        Union[
            "Disease",
            "DiseaseClassificationChoice",
            "Event",
            "EventClassification",
            "EventClassificationChoice",
            "Examination",
            "ExaminationIndication",
            "Finding",
            "FindingIntervention",
            "FindingClassification",
            "FindingClassificationChoice",
            "FindingClassificationType",
            "LabValue",
            "Medication",
            "MedicationIndication",
            "MedicationIntakeTime",  # Added MedicationIntakeTime
            "PatientDisease",
            "PatientEvent",
            "PatientExamination",
            "PatientFinding",
            "PatientFindingIntervention",
            "PatientFindingClassification",
            "PatientLabValue",
            "PatientMedicationSchedule",  # Added PatientMedicationSchedule
        ]
    ]:
        """
        Return the list of model classes that are expected as input for evaluating this requirement.

        The returned models correspond to the requirement types linked to this requirement, mapped via the internal data model dictionary.
        """
        req_types = self.requirement_types.all()
        req_type_names = [_.name for _ in req_types]

        expected_models = [self.data_model_dict[_] for _ in req_type_names]
        # e.g. [PatientExamination, PatientFinding]
        return expected_models

    @property
    def links(self) -> "RequirementLinks":
        """
        Return a RequirementLinks object containing all non-null related model instances for this requirement.

        The returned object provides structured access to all associated entities, such as examinations, diseases, findings, classifications, interventions, medications, and related choices, aggregated from the requirement's many-to-many fields.
        """
        # requirement_sets is not part of RequirementLinks (avoids circular import); collect other related models
        models_dict = RequirementLinks(
            examinations=[_ for _ in self.examinations.all() if _ is not None],
            examination_indications=[
                _ for _ in self.examination_indications.all() if _ is not None
            ],
            lab_values=[_ for _ in self.lab_values.all() if _ is not None],
            diseases=[_ for _ in self.diseases.all() if _ is not None],
            disease_classification_choices=[
                _ for _ in self.disease_classification_choices.all() if _ is not None
            ],
            events=[_ for _ in self.events.all() if _ is not None],
            findings=[_ for _ in self.findings.all() if _ is not None],
            finding_classifications=[
                _ for _ in self.finding_classifications.all() if _ is not None
            ],
            finding_classification_choices=[
                _ for _ in self.finding_classification_choices.all() if _ is not None
            ],
            finding_interventions=[
                _ for _ in self.finding_interventions.all() if _ is not None
            ],
            medications=[_ for _ in self.medications.all() if _ is not None],
            medication_indications=[
                _ for _ in self.medication_indications.all() if _ is not None
            ],
            medication_intake_times=[
                _ for _ in self.medication_intake_times.all() if _ is not None
            ],
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

    def evaluate(self, input_obj):
        """
        Evaluates whether the requirement is satisfied for the given input models using linked operators and gender constraints.

        Args:
            *args: Instances or QuerySets of expected model classes to be evaluated. Each must have a `.links` property returning a `RequirementLinks` object.
            mode: Evaluation mode; "strict" requires all operators to pass, "loose" requires any operator to pass.
            **kwargs: Additional keyword arguments passed to operator evaluations.

        Returns:
            True if the requirement is satisfied according to the specified mode, linked operators, and gender restrictions; otherwise, False.

        Raises:
            ValueError: If an invalid mode is provided.
            TypeError: If an input is not an instance or QuerySet of expected models, or lacks a valid `.links` attribute.

        If the requirement specifies genders, only input containing a patient with a matching gender will be considered valid for evaluation.
        """
        is_valid: bool = False

        requirement_req_links = self.active_links

        # expected_models = self.expected_models

        operator = self.operator
        assert isinstance(operator, RequirementOperator)

        operator_instructions = self.operator_instructions_parsed

        is_valid = operator.evaluate(input_links)

        return is_valid

    def evaluate_with_details(self, *args, mode: str, **kwargs) -> Tuple[bool, str]:
        """
        Evaluates whether the requirement is satisfied for the given input models using linked operators and gender constraints.

        Args:
            *args: Instances or QuerySets of expected model classes to be evaluated. Each must have a `.links` property returning a `RequirementLinks` object.
            mode: Evaluation mode; "strict" requires all operators to pass, "loose" requires any operator to pass.
            **kwargs: Additional keyword arguments passed to operator evaluations.

        Returns:
            (met, details):
                met: True/False, ob die Voraussetzung erfüllt ist
                details: menschenlesbare Erklärung (für UI geeignet)

        Raises:
            RequirementEvaluationError:
                - bei ungültigem Modus
                - bei komplett falschen Input-Typen / fehlender .links-Struktur
        """
        from endoreg_db.models.requirement.requirement_error import (
            RequirementEvaluationError,
        )

        # --- Mode validieren -------------------------------------------------
        if mode not in ["strict", "loose"]:
            raise RequirementEvaluationError(
                requirement=self,
                code="INVALID_MODE",
                technical_message=f"Invalid mode: {mode}. Use 'strict' or 'loose'.",
                user_message=(
                    "Diese Voraussetzung ist intern mit einem ungültigen Bewertungsmodus konfiguriert und kann aktuell nicht korrekt geprüft werden."
                ),
            )

        evaluate_result_list_func = all if mode == "strict" else any

        requirement_req_links = self.links
        expected_models = self.expected_models

        operators = list(self.operators.all())
        has_operators = bool(operators)
        requirement_has_conditions = bool(requirement_req_links.active())
        queryset_mode, queryset_min_count = self._resolve_queryset_config(kwargs)

        # --- Helper für Typprüfung ------------------------------------------
        def _is_expected_instance(obj) -> bool:
            for cls in expected_models:
                if isinstance(cls, type):
                    try:
                        if isinstance(obj, cls):
                            return True
                    except Exception:
                        # cls might nicht runtime-kompatibel sein
                        continue
            return False

        def _is_queryset_of_expected(qs) -> bool:
            if not isinstance(qs, models.QuerySet) or not hasattr(qs, "model"):
                return False
            for cls in expected_models:
                if isinstance(cls, type):
                    try:
                        if issubclass(qs.model, cls):
                            return True
                    except Exception:
                        continue
            return False

        # --- RequirementLinks aus allen Inputs aggregieren -------------------
        aggregated_input_links_data: dict = {}
        processed_inputs_count = 0

        for _input in args:
            if not _is_expected_instance(_input):
                # QuerySet von erwarteten Typen erlauben
                if _is_queryset_of_expected(_input):
                    if not _input.exists():
                        # leeres QS -> je nach QS-Mode sofort nicht erfüllt
                        if queryset_mode == "all":
                            return (
                                False,
                                "Für diese Voraussetzung müssen alle passenden Einträge vorliegen, aber es wurden keine entsprechenden Datensätze gefunden.",
                            )
                        if queryset_mode == "min_count":
                            required = (
                                queryset_min_count
                                if queryset_min_count is not None
                                else 1
                            )
                            if required > 0:
                                return (
                                    False,
                                    f"Für diese Voraussetzung werden mindestens {required} passende Einträge benötigt, es wurden jedoch keine gefunden.",
                                )
                        # queryset_mode == "any" bei leerem QS -> neutral (keine zusätzliche Einschränkung)
                        continue

                    queryset_results: List[bool] = []
                    queryset_true_count = 0
                    queryset_item_count = 0

                    for item in _input:
                        if not hasattr(item, "links") or not isinstance(
                            item.links, RequirementLinks
                        ):
                            raise RequirementEvaluationError(
                                requirement=self,
                                code="MISSING_LINKS_ATTR",
                                technical_message=(
                                    f"Item {item} of type {type(item)} in QuerySet does not have a valid .links attribute of type RequirementLinks."
                                ),
                                user_message=(
                                    "Für einen Datensatz fehlen die intern benötigten Verknüpfungen, "
                                    "sodass diese Voraussetzung nicht korrekt geprüft werden kann."
                                ),
                                meta={"item_type": str(type(item))},
                            )

                        item_active_links = item.links.active()
                        item_input_links = RequirementLinks(**item_active_links)

                        # Links sammeln
                        for link_key, link_list in item_active_links.items():
                            if link_key not in aggregated_input_links_data:
                                aggregated_input_links_data[link_key] = []
                            aggregated_input_links_data[link_key].extend(link_list)

                        per_item_args = tuple(
                            item if arg is _input else arg for arg in args
                        )
                        op_kwargs = kwargs.copy()
                        op_kwargs["requirement"] = self
                        op_kwargs["original_input_args"] = per_item_args

                        if has_operators:
                            item_operator_results: List[bool] = []
                            for operator in operators:
                                try:
                                    operator_result = operator.evaluate(
                                        requirement_links=requirement_req_links,
                                        input_links=item_input_links,
                                        **op_kwargs,
                                    )
                                    item_operator_results.append(operator_result)
                                except Exception as exc:
                                    logger.debug(
                                        "Operator %s evaluation failed for item %s: %s",
                                        getattr(operator, "name", "unknown"),
                                        item,
                                        exc,
                                    )
                                    item_operator_results.append(False)
                            item_result = (
                                evaluate_result_list_func(item_operator_results)
                                if item_operator_results
                                else True
                            )
                        else:
                            # keine Operatoren -> Bedingung erfüllt, wenn Requirement selbst keine Bedingungen hat
                            item_result = not requirement_has_conditions

                        queryset_results.append(item_result)
                        if item_result:
                            queryset_true_count += 1
                        queryset_item_count += 1
                        processed_inputs_count += 1

                    # QS-Modus nach Auswertung anwenden
                    if queryset_mode == "all":
                        if queryset_item_count == 0 or not all(queryset_results):
                            return (
                                False,
                                "Für diese Voraussetzung müssen alle relevanten Einträge die Bedingung erfüllen.",
                            )
                    elif queryset_mode == "min_count":
                        required = (
                            queryset_min_count if queryset_min_count is not None else 1
                        )
                        if queryset_true_count < max(required, 0):
                            return (
                                False,
                                f"Für diese Voraussetzung werden mindestens {max(required, 0)} passende Einträge benötigt (gefunden: {queryset_true_count}).",
                            )
                    # queryset_mode == "any": keine zusätzliche Einschränkung
                    continue

                # Weder Instanz noch QS eines erwarteten Modells -> Konfig-/Aufruf-Fehler
                raise RequirementEvaluationError(
                    requirement=self,
                    code="INVALID_INPUT_TYPE",
                    technical_message=(
                        f"Input type {type(_input)} is not among expected models: {self.expected_models} nor a QuerySet of expected models."
                    ),
                    user_message=(
                        "Diese Voraussetzung wurde mit einem nicht passenden Datentyp aufgerufen und kann aktuell nicht korrekt geprüft werden."
                    ),
                    meta={"input_type": str(type(_input))},
                )

            # Einzelinstanz erwarteten Typs
            if not hasattr(_input, "links") or not isinstance(
                _input.links, RequirementLinks
            ):
                raise RequirementEvaluationError(
                    requirement=self,
                    code="MISSING_LINKS_ATTR",
                    technical_message=(
                        f"Input {_input} of type {type(_input)} does not have a valid .links attribute of type RequirementLinks."
                    ),
                    user_message=(
                        "Für die Auswertung dieser Voraussetzung fehlen die intern benötigten Verknüpfungsinformationen."
                    ),
                    meta={"input_type": str(type(_input))},
                )

            active_input_links = _input.links.active()
            for link_key, link_list in active_input_links.items():
                if link_key not in aggregated_input_links_data:
                    aggregated_input_links_data[link_key] = []
                aggregated_input_links_data[link_key].extend(link_list)
            processed_inputs_count += 1

        # Wenn es zwar *args gibt, aber alles leer/irrelevant war, lassen wir das weiterlaufen.
        # Operatoren sehen dann ggf. ein leeres final_input_links.

        # Deduplizieren der aggregierten Links
        for key in aggregated_input_links_data:
            try:
                aggregated_input_links_data[key] = list(
                    dict.fromkeys(aggregated_input_links_data[key])
                )
            except TypeError:
                # Fallback für nicht-hashbare Items
                tmp: list = []
                for item in aggregated_input_links_data[key]:
                    if item not in tmp:
                        tmp.append(item)
                aggregated_input_links_data[key] = tmp

        final_input_links = RequirementLinks(**aggregated_input_links_data)

        # --- Gender-Check ----------------------------------------------------
        genders_exist = self.genders.exists()
        if genders_exist:
            from endoreg_db.models.administration.person.patient import Patient

            patient = None
            for arg in args:
                if isinstance(arg, Patient):
                    patient = arg
                    break

            if patient is None or patient.gender is None:
                return (
                    False,
                    "Für diese Voraussetzung ist ein hinterlegtes Geschlecht des Patienten erforderlich.",
                )

            if not self.genders.filter(pk=patient.gender.pk).exists():
                return (
                    False,
                    "Diese Voraussetzung gilt nur für bestimmte Geschlechter und ist für diesen Patienten nicht erfüllt.",
                )

        # --- Fall: keine Operatoren -----------------------------------------
        if not has_operators:
            if not requirement_has_conditions:
                return True, "Keine Operatoren für die Bewertung erforderlich."
            return (
                False,
                "Die Voraussetzung besitzt Bedingungen, aber keinen Operator zur Auswertung.",
            )

        # --- Operatoren anwenden --------------------------------------------
        operator_results: List[bool] = []
        operator_details: List[str] = []

        for operator in operators:
            op_kwargs = kwargs.copy()
            op_kwargs["requirement"] = self
            op_kwargs["original_input_args"] = args

            try:
                operator_result = operator.evaluate(
                    requirement_links=requirement_req_links,
                    input_links=final_input_links,
                    **op_kwargs,
                )
                operator_results.append(operator_result)
                operator_details.append(
                    f"{operator.name}: {'erfüllt' if operator_result else 'nicht erfüllt'}"
                )
            except Exception as e:
                operator_results.append(False)
                operator_details.append(f"{operator.name}: technischer Fehler ({e})")
                logger.debug(
                    "Operator %s evaluation failed for requirement %s: %s",
                    getattr(operator, "name", "unknown"),
                    getattr(self, "name", "unknown"),
                    e,
                )

        is_valid = evaluate_result_list_func(operator_results)

        # --- Detailtext bauen -----------------------------------------------
        if not operator_results:
            details = "Keine Operatoren für die Bewertung verfügbar."
        elif len(operator_results) == 1:
            details = operator_details[0]
        else:
            failed_details = [
                detail
                for detail, result in zip(operator_details, operator_results)
                if not result
            ]
            if failed_details:
                details = "; ".join(failed_details)
            else:
                details = "Alle verknüpften Bedingungen sind erfüllt."

        # Arbeitsverzeichnis als Debug-Helfer anhängen (optional)
        try:
            cwd = run("pwd", capture_output=True, text=True).stdout.strip()
            details = f"{details}\ncwd: {cwd}"
        except Exception:
            # nicht kritisch
            pass

        return bool(is_valid), details
