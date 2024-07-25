from django.core.management.base import BaseCommand
from endoreg_db.models import (
    CaseTemplateRuleType,
    CaseTemplateRuleValueType,
    CaseTemplateRuleValue,
    CaseTemplateRule,
    CaseTemplateType,
    CaseTemplate,
    SingleCategoricalValueDistribution,
    NumericValueDistribution,
    MultipleCategoricalValueDistribution,
    DateValueDistribution,

    QuizQuestionType,
    QuizAnswerType,
    QuizQuestion,
    QuizAnswer,

    # Other models for ForeignKeys
    Unit, Center
)
from collections import OrderedDict

from ...utils import load_model_data_from_yaml
from ...data import (
    CASE_TEMPLATE_RULE_TYPE_DATA_DIR,
    CASE_TEMPLATE_RULE_DATA_DIR,
    CASE_TEMPLATE_RULE_VALUE_TYPE_DATA_DIR,
    CASE_TEMPLATE_RULE_VALUE_DATA_DIR,
    CASE_TEMPLATE_TYPE_DATA_DIR,
    CASE_TEMPLATE_DATA_DIR,
)

IMPORT_METADATA = OrderedDict({
    CaseTemplateRuleType.__name__: {
        "dir": CASE_TEMPLATE_RULE_TYPE_DATA_DIR,
        "model": CaseTemplateRuleType, 
        "foreign_keys": [], 
        "foreign_key_models": [] 
    },
    CaseTemplateRuleValueType.__name__: {
        "dir": CASE_TEMPLATE_RULE_VALUE_TYPE_DATA_DIR,
        "model": CaseTemplateRuleValueType, 
        "foreign_keys": [], 
        "foreign_key_models": [] 
    },
    CaseTemplateRuleValue.__name__: {
        "dir": CASE_TEMPLATE_RULE_VALUE_DATA_DIR,
        "model": CaseTemplateRuleValue, 
        "foreign_keys": ["value_type"], 
        "foreign_key_models": [CaseTemplateRuleValueType] 
    },
    CaseTemplateRule.__name__: {
        "dir": CASE_TEMPLATE_RULE_DATA_DIR,
        "model": CaseTemplateRule, 
        "foreign_keys": [
            "rule_type", 
            # "rule_values",
            "chained_rules", 
            "value_type",
            "single_categorical_value_distribution",
            "numerical_value_distribution",
            "multiple_categorical_value_distribution",
            "date_value_distribution"
        ], 
        "foreign_key_models": [
            CaseTemplateRuleType,
            # CaseTemplateRuleValue,
            CaseTemplateRule,
            CaseTemplateRuleValueType,
            SingleCategoricalValueDistribution,
            NumericValueDistribution,
            MultipleCategoricalValueDistribution,
            DateValueDistribution

        ] 
    },
    CaseTemplateType.__name__: {
        "dir": CASE_TEMPLATE_TYPE_DATA_DIR,
        "model": CaseTemplateType, 
        "foreign_keys": [], 
        "foreign_key_models": [] 
    },
    CaseTemplate.__name__: {
        "dir": CASE_TEMPLATE_DATA_DIR,
        "model": CaseTemplate, 
        "foreign_keys": ["template_type", "rules"], 
        "foreign_key_models": [CaseTemplateType, CaseTemplateRule] 
    },
    
})

class Command(BaseCommand):
    help = """Load all .yaml files in the data/intervention directory
    into the Intervention and InterventionType model"""

    def add_arguments(self, parser):
        parser.add_argument(
            '--verbose',
            action='store_true',
            help='Display verbose output',
        )

    def handle(self, *args, **options):
        verbose = options['verbose']
        for model_name in IMPORT_METADATA.keys():
            _metadata = IMPORT_METADATA[model_name]
            load_model_data_from_yaml(
                self,
                model_name,
                _metadata,
                verbose
            )