from logging import getLogger  # Added logger
from typing import TYPE_CHECKING

from django.db import models

# see how operator evaluation function is fetched, add to docs #TODO
# endoreg_db/utils/requirement_operator_logic/model_evaluators.py

if TYPE_CHECKING:
    from endoreg_db.utils.links.requirement_link import RequirementLinks

    from .requirement import Requirement  # Added Requirement import for type hint

logger = getLogger(__name__)  # Added logger instance


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


class RequirementOperator(models.Model):
    """
    A class representing a requirement operator.

    Attributes:
        name (str): The name of the requirement operator.
        description (str): A description of the requirement operator.
    """

    name = models.CharField(max_length=100, unique=True)
    description = models.TextField(blank=True, null=True)
    evaluation_function_name = models.CharField(max_length=255, blank=True, null=True)  # Added field

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

    def evaluate(self, requirement_links: "RequirementLinks", input_links: "RequirementLinks", **kwargs) -> bool:  # Changed signature
        """
        Evaluates the requirement operator against the provided requirement links and input_links.

        Args:
            requirement_links: The RequirementLinks object from the Requirement model.
            input_links: The aggregated RequirementLinks object from the input arguments.
            **kwargs: Additional keyword arguments for specific operator logic.
                        For lab value operators, this includes 'requirement' (the Requirement model instance).
                        The 'requirement' instance is now passed for all operators.

        Returns:
            True if the condition defined by the operator is met, False otherwise.

        Raises:
            NotImplementedError: If the evaluation logic for the operator's name is not implemented.
        """
        # kwargs should contain 'requirement' (the Requirement instance) passed from Requirement.evaluate()
        if self.evaluation_function_name:
            eval_func = getattr(self, self.evaluation_function_name, None)
            if eval_func and callable(eval_func):
                result = eval_func(requirement_links=requirement_links, input_links=input_links, **kwargs)
                assert isinstance(result, bool), (
                    f"Evaluation function '{self.evaluation_function_name}' for operator '{self.name}' must return a boolean value."
                )
                return result
            else:
                logger.error(
                    f"Evaluation function '{self.evaluation_function_name}' not found or not callable on {self.__class__.__name__} for operator '{self.name}'."
                )
                raise NotImplementedError(f"Evaluation function '{self.evaluation_function_name}' not implemented correctly for operator '{self.name}'.")
        else:
            # Fallback to the central dispatcher if no specific function name is provided
            from endoreg_db.utils.requirement_operator_logic.model_evaluators import dispatch_operator_evaluation

            return dispatch_operator_evaluation(
                operator_name=self.name,
                requirement_links=requirement_links,
                input_links=input_links,
                operator_instance=self,  # Pass the operator instance
                **kwargs,
            )

    from ..medical.medication import MedicationSchedule as MedicationScheduleTemplate  # Added with alias
    from ..medical.patient.patient_medication import PatientMedication  # Added

    def _evaluate_patient_medication_schedule_matches_template(
        self,
        requirement_links: "RequirementLinks",
        input_links: "RequirementLinks",
        requirement: "Requirement",  # Added requirement
        **kwargs,
    ) -> bool:
        """
        Checks if any PatientMedication in the input PatientMedicationSchedule
        matches the profile (medication, dose, unit, intake times)
        of any MedicationSchedule template linked to the requirement.
        """
        # Get MedicationSchedule templates from the requirement
        req_schedule_templates = requirement_links.medication_schedules
        if not req_schedule_templates:
            # If the requirement doesn't specify any templates to match against,
            # it's ambiguous. Consider this a non-match.
            return False

        # Get PatientMedication instances from the input (derived from PatientMedicationSchedule.links)
        input_patient_medications = input_links.patient_medications
        if not input_patient_medications:
            # If the input schedule has no medications, it cannot match any template.
            return False

        for pm_instance in input_patient_medications:
            pm_intake_times_set = set(pm_instance.intake_times.all())
            for schedule_template in req_schedule_templates:
                template_intake_times_set = set(schedule_template.intake_times.all())

                # Check for profile match
                medication_match = pm_instance.medication == schedule_template.medication
                dose_match = pm_instance.dosage == schedule_template.dose
                unit_match = pm_instance.unit == schedule_template.unit
                intake_times_match = pm_intake_times_set == template_intake_times_set

                # Debugging output (optional, can be removed)
                # print(f"Comparing PM ID {pm_instance.id} with Template {schedule_template.name}:")
                # print(f"  Medication: {pm_instance.medication} vs {schedule_template.medication} -> {medication_match}")
                # print(f"  Dose: {pm_instance.dosage} vs {schedule_template.dose} -> {dose_match}")
                # print(f"  Unit: {pm_instance.unit} vs {schedule_template.unit} -> {unit_match}")
                # print(f"  Intake Times: {pm_intake_times_set} vs {template_intake_times_set} -> {intake_times_match}")

                if medication_match and dose_match and unit_match and intake_times_match:
                    return True  # Found a match

        return False  # No PatientMedication matched any template
