from .rule import Rule
import random
from django.core.exceptions import ValidationError

class RuleApplicator:
    """
    A class to apply different types of rules to a Django model instance based on the Rule configuration.
    """

    def get_rule_by_name(self, rule_name):
        """
        A helper method to fetch a rule by name. This can be further customized.
        """
        # get rule by name which is natural key
        rule = Rule.objects.get_by_natural_key(rule_name)
        return rule


    def apply(self, obj, rule):
        """
        Applies a specified rule to an object based on the rule type and attributes.

        Parameters:
        obj (Django model instance): The object to which the rule is applied.
        rule (Rule): The rule instance containing the rule_type and attribute details.
        """
        rule_type_method = self.get_rule_type_method(rule.rule_type.name)
        if rule_type_method:
            rule_type_method(obj, rule)

        else:
            raise ValueError(f"Unsupported rule type: {rule.rule_type.name}")
        
    def apply_rule_by_name(self, obj, rule_name):
        """
        A helper method to apply a rule and get a value. This can be further customized.
        """
        rule = self.get_rule_by_name(rule_name)
        self.apply(obj, rule)

    def apply_rules_by_name(self, obj, rule_names):
        """
        A helper method to apply multiple rules and get values. This can be further customized.
        """
        rules = [self.get_rule_by_name(rule_name) for rule_name in rule_names]
        for rule in rules:
            self.apply(obj, rule)
            

    def get_rule_type_method(self, rule_type_name):
        """
        Maps rule type name to the corresponding method.
        """
        return getattr(self, f"handle_{rule_type_name}", None)

    def parse_attribute_path(self, obj, attribute_key):
        """
        Parses the attribute path and applies the value to the correct attribute of a nested object.
        """
        parts = attribute_key.split('.')
        model_name = parts[0]
        if model_name.lower() != obj.__class__.__name__.lower():
            raise ValidationError(f"Model type mismatch: expected {model_name}, got {obj.__class__.__name__}")

        # Navigate through the nested attributes
        target = obj
        for part in parts[1:-1]:
            target = getattr(target, part)

        return target, parts[-1]

    def set_attribute_value(self, obj, rule):
        """
        Generic method to set attribute value considering nested paths.
        """
        target, attribute = self.parse_attribute_path(obj, rule.attribute_key)
        setattr(target, attribute, rule.attribute_dict['value'])
        target.save()

    #####

    def handle_case_attribute_set_value(self, obj, rule):
        """
        Sets a specific attribute to a given value.
        """
        self.set_attribute_value(obj, rule.attribute_key, rule.attribute_dict["value"])

    def handle_case_attribute_set_value_range_uniform(self, obj, rule):
        """
        Sets an attribute to a value within a specified range, selected uniformly.
        """
        if not hasattr(rule, 'attribute_dtype') or rule.attribute_dtype.name not in ['numeric', 'ordered_categorical']:
            raise ValidationError("Attribute dtype must be numeric or ordered_categorical")
        min_val = rule.attribute_dict["value_min"]
        max_val = rule.attribute_dict["value_max"]
        value = random.uniform(min_val, max_val)
        self.set_attribute_value(obj, rule.attribute_key, value)

    def handle_case_attribute_set_value_range_norm_dist(self, obj, rule):
        """
        Sets an attribute to a value within a specified range, based on a normal distribution.
        """
        if rule.attribute_dtype.name in ['float', "integer"]:
            raise ValidationError("Attribute dtype must be float or integer")
        mean = rule.attribute_dict["value_mean"]
        std_dev = rule.attribute_dict["value_sd"]
        value = random.normalvariate(mean, std_dev)
        self.set_attribute_value(obj, rule.attribute_key, value)
        

    def handle_case_attribute_set_from_list_uniform(self, obj, rule):
        """
        Selects an attribute value uniformly from a list of choices.
        """
        choices = rule.attribute_dict["value_choices"]
        value = random.choice(choices)
        self.set_attribute_value(obj, rule.attribute_key, value)

    def handle_case_attribute_set_from_prop_tuple_list(self, obj, rule):
        """
        Selects an attribute value based on a list of value-probability tuples.
        """
        prop_list = rule.attribute_dict["value_prop_tuple_list"]
        total = sum(prop for _, prop in prop_list)
        pick = random.uniform(0, total)
        current = 0
        for value, prop in prop_list:
            current += prop
            if current > pick:
                self.set_attribute_value(obj, rule.attribute_key, value)
                break


    #####

    def handle_case_add_patient(self, obj, rule):
        """Function to add a patient to a case. If the patient already exists, raise an error. Requires the following
        rules in the rule_dict:
        - gender_rule
        - dob_rule
        - event_rules
        - disease_rules
        """
        from endoreg_db.models import Patient, PatientEvent, PatientDisease, Case

        # Check if the 
        obj:Case = obj
        if obj.patient:
            raise ValueError("Patient already exists in the case")
        
        patient = Patient()
        self.apply_rule_by_name(patient, rule.attribute_dict["patient_gender_rule"])
        self.apply_rule_by_name(patient, rule.attribute_dict["patient_dob_rule"])
        self.apply_rules_by_name(patient, rule.attribute_dict["patient_event_rules"])
        self.apply_rules_by_name(patient, rule.attribute_dict["patient_disease_rules"])

    def handle_case_add_polyp(self, obj, rule):
        """
        Adds a polyp to the case, processing various polyp characteristics. Requires Rules for each attribute:
        - polyp_location_organ_rule (single rule)
        - polyp_location_organ_part_rule (single rule)
        - polyp_morphology_classification_shape_choices_rule (list of rules)
        - polyp_morphology_classification_chromo_choices_rule (list of rules)
        - polyp_intervention_type_rule (single rule)
        - polyp_intervention_instrument_rule (single rule)
        - polyp_size_mm_rule (single rule)

        """
        # Example: create and add a polyp instance based on the rules
        from endoreg_db.models import Polyp  # Ensure you have a Polyp model and adjust the import
        from endoreg_db.models import Location
        from endoreg_db.models import EndoscopicIntervention
        from endoreg_db.models import PolypMorphology

        assert obj.examination.type != None, "Examination type is required"

        location = Location()
        location.organ = self.apply_rule_by_name(location, rule.attribute_dict["polyp_location_organ_rule"])
        location.organ_part = self.apply_rule_by_name(location, rule.attribute_dict["polyp_location_organ_part_rule"])
        location.save()

        morphology = PolypMorphology()
        # M2M to ClassificationChoice (linked to Classification which has name as natural key)
        morphology.shape_classification_choices = self.apply_rules_by_name(morphology, rule.attribute_dict["polyp_morphology_classification_shape_choices_rules"])
        # M2M to ClassificationChoice (linked to Classification which has name as natural key)
        morphology.chromo_classification_choices = self.apply_rules_by_name(morphology, rule.attribute_dict["polyp_morphology_classification_chromo_choices_rules"])
        morphology.save()

        intervention = EndoscopicIntervention()
        intervention.type = self.apply_rule_by_name(intervention, rule.attribute_dict["polyp_intervention_type_rule"])
        intervention.instrument = self.apply_rule_by_name(intervention, rule.attribute_dict["polyp_intervention_instrument_rule"])
        intervention.save()

        polyp = Polyp()
        polyp.size_mm = self.apply_rule_by_name(polyp, rule.attribute_dict["polyp_size_mm_rule"])
        polyp.location = location
        polyp.morphology = morphology
        polyp.intervention = intervention
        
        polyp.save()

        obj.polyps.add(polyp)
        obj.save()


    # def handle_case_add_esophageal_varices(self, obj, rule):
    #     """
    #     Handles adding esophageal varices to a case based on the rule.
    #     """
    #     # Similar to the polyp case, create and add esophageal varices based on the attributes
    #     from endoreg_db.models import EsophagealVarices  # Ensure this model exists
    #     varices = EsophagealVarices()
    #     varices.origin = self.apply_rule_by_name(obj, rule.attribute_dict["esophageal_varices_origin_rule"])
    #     varices.classification = self.apply_rule_by_name(obj, rule.attribute_dict["esophageal_varices_classification_rule"])
    #     varices.location_extent = self.apply_rule_by_name(obj, rule.attribute_dict["esophageal_varices_location_extent_rule"])
    #     varices.save()
    #     # Assuming a relationship setup
    #     obj.esophageal_varices.add(varices)
    #     obj.save()


    
    
        
