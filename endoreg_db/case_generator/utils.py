from endoreg_db.models import CaseTemplate
from endoreg_db.case_generator.case_generator import CaseGenerator

# TEMPLATE_NAME = "pre_endo-anticoagulation-af-low_risk"
TEMPLATE_NAME = "pre_default_screening_colonoscopy"

def fetch_template(template_name: str = DEFAULT_TEMPLATE_NAME) -> CaseTemplate:
    """
    Fetches a CaseTemplate by name.

    Args:
        template_name (str): The name of the template to fetch. Defaults to DEFAULT_TEMPLATE_NAME.

    Returns:
        CaseTemplate: The fetched CaseTemplate instance.
    """
    return CaseTemplate.objects.get(name=template_name)

def initialize_case_generator(template_name: str = DEFAULT_TEMPLATE_NAME) -> CaseGenerator:
    """
    Initializes a CaseGenerator with the specified template.

    Args:
        template_name (str): The name of the template to use. Defaults to DEFAULT_TEMPLATE_NAME.

    Returns:
        CaseGenerator: An instance of CaseGenerator initialized with the template.
    """
    template = fetch_template(template_name)
    return CaseGenerator(template)
