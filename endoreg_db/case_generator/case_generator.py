from endoreg_db.models import CaseTemplate, CaseTemplateRule, CaseTemplateRuleType
from endoreg_db.case_generator.lab_sample_factory import LabSampleFactory

DEFAULT_CASE_TEMPLATE_NAME = "pre_default_screening_colonoscopy"

class CaseGenerator:
    """
    Provides methods to generate cases based on a template.
    """

    def __init__(self, template: CaseTemplate = None):
        """
        Initializes the CaseGenerator with a template.

        Args:
            template (CaseTemplate, optional): The template to use for case generation. Defaults to the predefined template.
        """
        self.template = template or CaseTemplate.objects.get(name=DEFAULT_CASE_TEMPLATE_NAME)
        self.lab_sample_factory = LabSampleFactory()

        # Define available rule types
        rule_type_names = [
            "create-object",
            "set-field-default",
            "set-field-by-distribution",
            "set-field-by-value",
            "set-field-single-choice",
            "set-field-multiple-choice",
        ]
        self.available_rule_types = CaseTemplateRuleType.objects.filter(name__in=rule_type_names)

    def _validate_rule_type(self, rule_type: CaseTemplateRuleType):
        """
        Validates if the rule type is supported.

        Args:
            rule_type (CaseTemplateRuleType): The rule type to validate.

        Raises:
            ValueError: If the rule type is not supported.
        """
        if rule_type not in self.available_rule_types:
            raise ValueError(f"Rule type {rule_type} is not supported.")

    def _apply_create_object(self, rule: CaseTemplateRule, parent=None):
        """
        Applies a create-object rule to generate a model instance.

        Args:
            rule (CaseTemplateRule): The rule to apply.
            parent (Optional[Model]): The parent object for the rule.

        Returns:
            Model: The created model instance.
        """
        target_model = rule.get_target_model()
        extra_params = rule.extra_parameters or {}
        create_method_info = extra_params.get("create_method", {})

        assert create_method_info, "Create method must be set for a create-object rule."

        create_method = getattr(target_model, create_method_info["name"])
        kwargs = create_method_info.get("kwargs", {})

        if parent:
            kwargs[rule.parent_field] = parent

        target_instance = create_method(**kwargs)
        target_instance.save()

        for action in extra_params.get("actions", []):
            action_method = getattr(target_instance, action["name"])
            action_kwargs = action.get("kwargs", {})
            action_method(**action_kwargs)

        for chained_rule in rule.chained_rules.all():
            self.apply_rule(chained_rule, parent=target_instance)

        return target_instance

    def _apply_set_field_by_distribution(self, rule: CaseTemplateRule, parent):
        """
        Applies a set-field-by-distribution rule.

        Args:
            rule (CaseTemplateRule): The rule to apply.
            parent (Model): The parent object for the rule.

        Returns:
            Model: The updated parent object.
        """
        assert parent, "Parent must be provided for set-field-by-distribution rules."
        assert rule.target_field, "Target field must be specified for the rule."

        distribution = rule.get_distribution()
        value = distribution.generate_value()

        setattr(parent, rule.target_field, value)
        parent.save()
        return parent

    def apply_rule(self, rule: CaseTemplateRule, parent=None):
        """
        Applies a rule based on its type to generate a case.

        Args:
            rule (CaseTemplateRule): The rule to apply.
            parent (Optional[Model]): The parent object for the rule.

        Returns:
            Model: The case by applying the rule.
        """
        self._validate_rule_type(rule.rule_type)

        if rule.rule_type.name == "create-object":
            return self._apply_create_object(rule, parent)

        if rule.rule_type.name == "set-field-by-distribution":
            return self._apply_set_field_by_distribution(rule, parent)

        raise ValueError(f"Unsupported rule type: {rule.rule_type.name}")

    def generate_case(self, case_template: CaseTemplate = None):
        """
        Generates a case based on the provided or default template.

        Args:
            case_template (CaseTemplate, optional): The template to use for case generation. Defaults to None.

        Returns:
            Tuple[Model, Model]: The generated patient and medication schedule.
        """
        case_template = case_template or CaseTemplate.objects.get(name=DEFAULT_CASE_TEMPLATE_NAME)

        create_patient_rule = case_template.get_create_patient_rule()
        patient = self.apply_rule(create_patient_rule)

        medication_schedule_rule = case_template.get_create_patient_medication_schedule_rule()
        medication_schedule = self.apply_rule(medication_schedule_rule, parent=patient)

        return patient, medication_schedule

        # if not create_new_patient:
        #     raise NotImplementedError("Only new patients are supported at the moment.")
        # else:
        #     # TODO Implement patient rules
        #     patient_rules = None # all rules of type "create_patient"
        #     patient = self.generate_patient(patient_rules)

        # # Generate case based on template
        # rules = self.template.get_rules()
        # chained_rules = set()

        # for rule in rules:
        #     rule_chain = rule.get_all_downward_chained_rules()
        #     chained_rules.add(rule)
        #     chained_rules.update(rule_chain)

        # return chained_rules