from io import StringIO
from django.core.management import call_command
from django.test import TestCase

from endoreg_db.models import (
    CaseTemplate
)

from endoreg_db.case_generator.case_generator import CaseGenerator

DEFAULT_TEMPLATE_NAME = "pre_default_screening_colonoscopy"

RULE_ATTRIBUTES = [
    "name",
    "rule_type",
    # "parent_model",
    # "parent_field",
    # "target_model",
    # "target_field",
    # "rule_values",
    # "extra_parameters",
    # "value_type",
    "chained_rules"
]

def print_rule_attributes(rule):
    print("Rule:")
    print(rule)
    print("Attributes:")
    
    for attr in RULE_ATTRIBUTES:
        print(f"{attr}: {getattr(rule, attr)}")

class LoadGplayData(TestCase):
    def setUp(self):
        out = StringIO()
        call_command("load_gender_data", stdout=out)
        call_command("load_center_data", stdout=out)
        call_command("load_distribution_data", stdout=out)
        call_command("load_lab_value_data", stdout=out)
        call_command("load_g_play_data", stdout=out)

    def test_check_templates(self):
        templates = CaseTemplate.objects.all()
        assert templates
        # print("Case Templates:")
        # for template in templates:
        #     print(template)

    def test_check_default_template(self):
        template = CaseTemplate.objects.get(name=DEFAULT_TEMPLATE_NAME)
        assert template
        # print("Default Template:")
        # print(template)

        # print("Patient Create Rule:")
        patient_create_rule = template.get_create_patient_rule()
        assert patient_create_rule
        # print_rule_attributes(patient_create_rule)



        cg = CaseGenerator(template)

        # print("Testing create patient rule:")
        patient = cg.apply_rule(patient_create_rule)
        # print(patient)


        