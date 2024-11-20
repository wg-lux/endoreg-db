from endoreg_db.models import CaseTemplate, CaseTemplateRule, CaseTemplateRuleType
from endoreg_db.case_generator.lab_sample_factory import LabSampleFactory

DEFAULT_CASE_TEMPLATE_NAME = "pre_default_screening_colonoscopy"

class CaseGenerator:
    """
    Provides methods to generate cases based on a template.
    """
    def __init__(self, template=None):
        if not template:
            # get default template
            template = CaseTemplate.objects.get(name=DEFAULT_CASE_TEMPLATE_NAME)

        self.template = template

        self.lsf = LabSampleFactory()

        # define available rule types
        rule_types = [
            "create-object",
            "set-field-default",
            "set-field-by_distribution",
            "set_field_by_value"
            "set-field-single_choice",
            "set-field-multiple_choice",
        ]
        rule_types = CaseTemplateRuleType.objects.filter(name__in=rule_types)
        self.rule_types = rule_types

    def _check_rule_type(self, rule_type:CaseTemplateRuleType):
        if rule_type not in self.rule_types:
            raise ValueError(f"Rule type {rule_type} is not supported.")

    def apply_create_object_rule(self, rule:CaseTemplateRule, parent=None):      
        # get the django db model from the endoreg_db module based on the models name
        target_model = rule.get_target_model()

        print("Creating object of type: ", target_model)
        print("Rule", rule)
        print("Extra parameters: ", rule.extra_parameters)
        create_kwargs = rule.extra_parameters["create_method"]["kwargs"]

        extra_params = getattr(rule, "extra_parameters", {})
        create_method_dict:dict = extra_params.get("create_method", {})
        assert create_method_dict, "Create method must be set for rule with create-object."

        create_method = getattr(target_model, create_method_dict["name"])
        kwargs = create_method_dict.get("kwargs", {})

        if parent:
            parent_model_field = rule.parent_field
            kwargs[parent_model_field] = parent

        target_model_instance = create_method(**kwargs)
        target_model_instance.save()

        actions = extra_params.get("actions", [])
        for action in actions:
            print("Applying action: ", action)
            action_method = getattr(target_model_instance, action["name"])
            action_kwargs = action.get("kwargs", {})
            action_method(**action_kwargs)

        for chained_rule in rule.chained_rules.all():
            self.apply_rule(chained_rule, parent = target_model_instance)

        return target_model_instance

    def apply_set_field_by_distribution_rule(self, rule:CaseTemplateRule, parent=None):
        distribution = rule.get_distribution()
        value = distribution.generate_value()

        print(f"Setting field {rule.target_field} to {value}.")
        
        assert parent, "Parent must be set for rule with parent."
        assert rule.target_field, "Target field must be set for rule with parent."

        setattr(parent, rule.target_field, value)
        parent.save()
        return parent



    def apply_rule(self, rule:CaseTemplateRule, parent=None):
        """
        Applies a rule to generate a case.
        """
        # get necessary attributes like rule type and target model from rule
        rule_type = rule.rule_type

        # make sure rule type is one of the following:
        self._check_rule_type(rule_type)

        # apply rule based on rule type
        if rule_type.name == "create-object":
            result = self.apply_create_object_rule(rule, parent=parent)

        if rule_type.name == "set-field-by_distribution":
            result = self.apply_set_field_by_distribution_rule(rule, parent=parent)

        return result

    def generate_case(self,case_template:CaseTemplate=None):
        """
        Generates a case based on the template.
        
        """

        if not case_template:
            case_template = CaseTemplate.objects.get(name="pre_default_screening_colonoscopy")

        # Generate patient
        create_patient_rule:CaseTemplateRule = case_template.get_create_patient_rule()
        patient = self.apply_rule(create_patient_rule)

        # Generate Medication Schedule
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