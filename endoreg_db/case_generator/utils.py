from endoreg_db.models import (
    CaseTemplate
)
from endoreg_db.case_generator.case_generator import CaseGenerator

# TEMPLATE_NAME = "pre_endo-anticoagulation-af-low_risk"
TEMPLATE_NAME = "pre_default_screening_colonoscopy"

def get_template(template_name=TEMPLATE_NAME):
    template = CaseTemplate.objects.get(name=template_name)
    return template

def get_case_generator(template_name=TEMPLATE_NAME):
    template = get_template(template_name)
    case_generator = CaseGenerator(template)
    return case_generator
