# Django models to define CaseTemplate and CaseTemplateType models 
# (each with "name" field which acts as a natural key)
from .case_template_rule import CaseTemplateRule, CaseTemplateRuleType
from .case_template_rule_value import CaseTemplateRuleValue, CaseTemplateRuleValueType
from .case_template_type import CaseTemplateType
from .case_template import CaseTemplate