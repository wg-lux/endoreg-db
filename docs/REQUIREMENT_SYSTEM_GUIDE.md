# Requirement Evaluation System - Implementation & Development Guide

## Table of Contents
1. [Overview](#overview)
2. [Architecture](#architecture)
3. [Core Components](#core-components)
4. [Usage Examples](#usage-examples)
5. [Creating Requirements](#creating-requirements)
6. [Creating Requirement Sets](#creating-requirement-sets)
7. [Available Operators](#available-operators)
8. [Development Guide](#development-guide)
9. [Testing](#testing)
10. [Troubleshooting](#troubleshooting)

## Overview

The Requirement Evaluation System is a flexible framework for defining and evaluating complex medical requirements against patient data. It supports hierarchical requirement sets, multiple evaluation operators, and intelligent input routing based on data types.

### Key Features
- **Hierarchical Requirements**: Requirements can be grouped into sets that link to other sets
- **Multiple Evaluation Modes**: Support for `all`, `any`, `none`, `exactly_1`, etc.
- **Type-Aware Evaluation**: Automatically routes appropriate data to each requirement
- **Extensible Operators**: Easy to add new evaluation operators
- **YAML Configuration**: Requirements and sets defined in human-readable YAML files

## Architecture

```
RequirementSet
├── requirements: List[Requirement]
├── links_to_sets: List[RequirementSet]
├── requirement_set_type: RequirementSetType
└── evaluate(input_object) -> bool

Requirement
├── requirement_types: List[RequirementType]
├── operators: List[RequirementOperator]
├── findings, lab_values, etc.: Domain-specific fields
└── evaluate(*args) -> bool

RequirementOperator
├── evaluate(requirement_links, input_links, **kwargs) -> bool
└── Dispatched to specific evaluation functions
```

## Core Components

### 1. Requirements (`endoreg_db.models.requirement.Requirement`)

A Requirement defines a specific condition that must be met. It consists of:
- **requirement_types**: What kind of input data it expects (e.g., `patient`, `patient_finding`)
- **operators**: How to evaluate the condition (e.g., `models_match_all`, `age_gte`)
- **Domain fields**: Specific medical entities (findings, classifications, lab values, etc.)

### 2. Requirement Sets (`endoreg_db.models.requirement.RequirementSet`)

A RequirementSet groups requirements and other requirement sets with evaluation logic:
- **requirements**: Direct requirements to evaluate
- **links_to_sets**: Other requirement sets to include
- **requirement_set_type**: How to combine results (`all`, `any`, `none`, etc.)

### 3. Requirement Links (`endoreg_db.utils.links.RequirementLinks`)

RequirementLinks objects aggregate medical data from input models, providing a standardized interface for requirement evaluation.

## Usage Examples

### Basic Evaluation

```python
from endoreg_db.models import RequirementSet, Patient

# Get a requirement set
req_set = RequirementSet.objects.get(name="patient_age_generic")

# Evaluate against a patient
patient = Patient.objects.get(id=1)
result = req_set.evaluate(patient)  # Returns True/False
```

### Examination-Based Evaluation

```python
from endoreg_db.models import RequirementSet, PatientExamination

# Get requirement set for colonoscopy screening
req_set = RequirementSet.objects.get(name="colonoscopy_austria_screening_qa")

# Evaluate against an examination
examination = PatientExamination.objects.get(id=1)
result = req_set.evaluate(examination)  # Automatically routes data to appropriate requirements
```

### Individual Requirement Evaluation

```python
from endoreg_db.models import Requirement, PatientFinding

# Get a specific requirement
req = Requirement.objects.get(name="colonoscopy_austria_screening_examination_cecum_visualized")

# Evaluate against a finding
finding = PatientFinding.objects.get(id=1)
result = req.evaluate(finding)  # Returns True/False
```

## Creating Requirements

Requirements are defined in YAML files located in `endoreg_db/data/requirement/`. Each requirement specifies conditions for medical data evaluation.

### Basic Requirement Structure

```yaml
- model: endoreg_db.requirement
  fields:
    name: "requirement_unique_name"
    name_de: "German name"
    name_en: "English name"  
    description: "Description of what this requirement checks"
    requirement_types:
      - "expected_input_type"  # e.g., "patient", "patient_finding"
    operators:
      - "evaluation_operator"  # e.g., "models_match_all", "age_gte"
    # Domain-specific fields
    numeric_value: 18  # For age/numeric comparisons
    findings:
      - "finding_name"  # Medical findings to match
    finding_classifications:
      - "classification_name"  # Classifications to match
    finding_classification_choices:
      - "choice_name"  # Specific choices to match
```

### Requirement Types

The `requirement_types` field specifies what kind of input data the requirement expects:

- **`patient`**: Expects Patient instances
- **`patient_finding`**: Expects PatientFinding instances  
- **`patient_examination`**: Expects PatientExamination instances
- **`patient_lab_value`**: Expects PatientLabValue instances
- **`patient_disease`**: Expects PatientDisease instances

### Example Requirements

#### Age Requirement
```yaml
- model: endoreg_db.requirement
  fields:
    name: "patient_age_gte_18"
    name_de: "Patientenalter >= 18 Jahre"
    description: "Patient must be at least 18 years old"
    requirement_types:
      - "patient"
    operators:
      - "age_gte"
    numeric_value: 18
```

#### Finding-Based Requirement
```yaml
- model: endoreg_db.requirement
  fields:
    name: "colonoscopy_cecum_visualized"
    description: "Cecum must be visualized during colonoscopy"
    requirement_types:
      - "patient_finding"
    operators:
      - "models_match_all"
    findings:
      - "cecum"
    finding_classifications:
      - "visualized"  
    finding_classification_choices:
      - "yes"
```

#### Lab Value Requirement
```yaml
- model: endoreg_db.requirement
  fields:
    name: "lab_value_hb_normal"
    description: "Hemoglobin within normal range"
    requirement_types:
      - "patient_lab_value"
    operators:
      - "numeric_value_within_normal_range"
    lab_values:
      - "hemoglobin"
```

#### Gender Requirement
```yaml
- model: endoreg_db.requirement
  fields:
    name: "patient_gender_is_female"
    requirement_types:
      - "patient"
    operators:
      - "models_match_all"
    genders:
      - "female"
```

## Creating Requirement Sets

Requirement Sets are defined in YAML files located in `endoreg_db/data/requirement_set/`. They combine requirements and other requirement sets using logical operators.

### Basic Requirement Set Structure

```yaml
- models: endoreg_db.requirement_set
  fields:
    name: "unique_set_name"
    name_de: "German name"
    name_en: "English name"
    description: "Description of what this set evaluates"
    requirement_set_type: "evaluation_logic"  # all, any, none, exactly_1, etc.
    requirements:
      - "requirement_name_1"
      - "requirement_name_2"
    links_to_sets:
      - "other_requirement_set_name"
```

### Requirement Set Types

- **`all`**: All requirements/sets must be satisfied (AND logic)
- **`any`**: At least one requirement/set must be satisfied (OR logic)
- **`none`**: No requirements/sets should be satisfied (NOT logic)
- **`exactly_1`**: Exactly one requirement/set must be satisfied
- **`at_least_1`**: Same as `any`
- **`at_most_1`**: Zero or one requirement/set can be satisfied

### Example Requirement Sets

#### Simple Age Range
```yaml
- models: endoreg_db.requirement_set
  fields:
    name: "patient_age_adult"
    name_en: "Adult Patient Age"
    description: "Patient age between 18 and 100 years"
    requirement_set_type: "all"
    requirements:
      - "patient_age_gte_18"
      - "patient_age_lte_100"
```

#### Gender Selection
```yaml
- models: endoreg_db.requirement_set
  fields:
    name: "patient_gender_binary"
    name_en: "Binary Gender"
    description: "Patient is male or female"
    requirement_set_type: "any"
    requirements:
      - "patient_gender_is_male"
      - "patient_gender_is_female"
```

#### Complex Medical Screening
```yaml
- models: endoreg_db.requirement_set
  fields:
    name: "colonoscopy_austria_screening_qa"
    name_de: "Vorsorge Koloskopie Österreich QA"
    description: "Complete quality assurance for Austrian colonoscopy screening"
    requirement_set_type: "all"
    links_to_sets:
      - "colonoscopy_austria_screening_finding_polyp_required_classifications"
      - "colonoscopy_austria_screening_required_patient_information"
      - "colonoscopy_austria_screening_examination_required_findings"
```

#### Examination Completeness
```yaml
- models: endoreg_db.requirement_set
  fields:
    name: "colonoscopy_examination_complete"
    name_en: "Colonoscopy Complete"
    description: "Either cecum visualized OR reason for incompleteness documented"
    requirement_set_type: "any"
    requirements:
      - "colonoscopy_austria_screening_examination_cecum_visualized"
      - "colonoscopy_austria_screening_examination_reason_incomplete"
```

## Available Operators

### Model Matching Operators

#### `models_match_all`
Requires ALL specified models to be present in the input.

**Example**: Patient finding must have cecum + visualized classification + "yes" choice
```yaml
operators: ["models_match_all"]
findings: ["cecum"]
finding_classifications: ["visualized"]
finding_classification_choices: ["yes"]
```

#### `models_match_any`
Requires ANY of the specified models to be present in the input.

**Example**: Patient must have either diabetes or hypertension
```yaml
operators: ["models_match_any"]
diseases: ["diabetes", "hypertension"]
```

### Age Operators

#### `age_gte`
Patient age must be greater than or equal to `numeric_value`.
```yaml
operators: ["age_gte"]
numeric_value: 18
```

#### `age_lte`
Patient age must be less than or equal to `numeric_value`.
```yaml
operators: ["age_lte"]
numeric_value: 100
```

### Lab Value Operators

#### `numeric_value_within_normal_range`
Lab value must be within the defined normal range.
```yaml
operators: ["numeric_value_within_normal_range"]
lab_values: ["hemoglobin"]
```

#### `numeric_value_gte`
Lab value must be greater than or equal to `numeric_value`.

#### `numeric_value_lte`
Lab value must be less than or equal to `numeric_value`.

### Temporal Operators

#### `models_match_any_in_timeframe`
Matches models within a specific timeframe relative to current date.
```yaml
operators: ["models_match_any_in_timeframe"]
events: ["surgery"]
numeric_value_min: -30  # 30 days ago
numeric_value_max: 0    # today
unit: "days"
```

## Development Guide

### Adding New Operators

1. **Implement the evaluation function** in `endoreg_db/utils/requirement_operator_logic/model_evaluators.py`:

```python
def _evaluate_new_operator(
    requirement_links: "RequirementLinks",
    input_links: "RequirementLinks",
    requirement: "Requirement",
    **kwargs
) -> bool:
    """
    Implement your evaluation logic here.
    
    Args:
        requirement_links: Data from the requirement definition
        input_links: Aggregated data from input objects
        requirement: The requirement instance for accessing numeric_value, etc.
        **kwargs: Additional parameters including original_input_args
        
    Returns:
        True if condition is met, False otherwise
    """
    # Your implementation here
    return False
```

2. **Register the operator** in the `dispatch_operator_evaluation` function:

```python
elif operator_name == "new_operator":
    if not isinstance(requirement, Requirement):
        raise ValueError("new_operator requires a valid 'requirement' instance in kwargs.")
    
    kwargs_for_eval = {k: v for k, v in kwargs.items() if k != 'requirement'}
    
    return _evaluate_new_operator(
        requirement_links=requirement_links,
        input_links=input_links,
        requirement=requirement,
        **kwargs_for_eval
    )
```

3. **Create the operator in YAML** (`endoreg_db/data/requirement_operator/model_operators.yaml`):

```yaml
- model: endoreg_db.requirement_operator
  fields:
    name: "new_operator"
    description: "Description of what this operator does"
```

### Extending RequirementLinks

To add support for new model types:

1. **Update RequirementLinks** in `endoreg_db/utils/links/requirement_link.py`:

```python
@dataclass
class RequirementLinks:
    # Add your new field
    new_model_instances: List["NewModel"] = field(default_factory=list)
    
    def active(self) -> Dict[str, List]:
        """Include new field in active links"""
        result = {}
        # ... existing fields ...
        if self.new_model_instances:
            result["new_model_instances"] = self.new_model_instances
        return result
```

2. **Update model links properties** to populate the new field in relevant models.

3. **Update requirement_type_parser.py** to map requirement type strings to model classes.

### Smart Input Routing

The system automatically routes input data to appropriate requirements based on their `requirement_types`. You can extend this in `RequirementSet._get_evaluation_input_for_requirement()`:

```python
def _get_evaluation_input_for_requirement(self, requirement, input_object):
    expected_models = requirement.expected_models
    
    # Add new routing logic
    if isinstance(input_object, NewParentModel):
        if ChildModel in expected_models:
            return input_object.child_objects.all()
    
    return input_object
```

## Testing

### Unit Tests

Create tests in `tests/requirement/` following the pattern:

```python
from django.test import TestCase
from endoreg_db.models import RequirementSet
from ..helpers.data_loader import load_data

class RequirementSetTest(TestCase):
    def setUp(self):
        load_data()  # Load YAML fixtures
        
    def test_requirement_evaluation(self):
        req_set = RequirementSet.objects.get(name="test_requirement_set")
        # Set up test data
        result = req_set.evaluate(test_input)
        self.assertTrue(result)
```

### Integration Tests

Test complete workflows with real data:

```python
def test_colonoscopy_screening_complete_workflow(self):
    # Create patient, examination, findings
    patient = generate_patient()
    examination = patient.create_examination("colonoscopy_austria_screening")
    
    # Add required findings and classifications
    cecum_finding = examination.create_finding(Finding.objects.get(name="cecum"))
    cecum_finding.add_classification(
        classification_id=...,
        choice_id=...
    )
    
    # Evaluate requirement set
    req_set = RequirementSet.objects.get(name="colonoscopy_austria_screening_qa")
    result = req_set.evaluate(examination)
    self.assertTrue(result)
```

## Troubleshooting

### Common Issues

#### 1. Type Mismatch Errors
```
TypeError: Input type <class 'PatientExamination'> is not among expected models: [<class 'PatientFinding'>]
```

**Solution**: The requirement expects a different input type. Check the requirement's `requirement_types` field and ensure you're passing the correct object type, or update the smart routing logic.

#### 2. Missing RequirementLinks
```
TypeError: Input does not have a valid .links attribute of type RequirementLinks.
```

**Solution**: Ensure the input model has a properly implemented `links` property that returns a `RequirementLinks` object.

#### 3. Operator Not Implemented
```
NotImplementedError: Evaluation logic for operator 'custom_operator' is not implemented.
```

**Solution**: Implement the operator function and register it in `dispatch_operator_evaluation()`.

#### 4. Empty QuerySet Evaluation
Requirements expecting specific data return `False` when evaluating empty QuerySets. This is expected behavior.

### Debugging

Enable debug logging for detailed evaluation traces:

```python
import logging
logging.getLogger('endoreg_db.utils.requirement_operator_logic.model_evaluators').setLevel(logging.DEBUG)
```

### Performance Considerations

- **Lazy Evaluation**: Requirements short-circuit on first match for `any` logic
- **QuerySet Optimization**: Use `select_related()` and `prefetch_related()` when loading input objects
- **Caching**: Consider caching requirement evaluation results for frequently accessed data

## File Locations

### YAML Configuration Files
- **Requirements**: `endoreg_db/data/requirement/*.yaml`
- **Requirement Sets**: `endoreg_db/data/requirement_set/*.yaml`
- **Requirement Operators**: `endoreg_db/data/requirement_operator/*.yaml`
- **Requirement Types**: `endoreg_db/data/requirement_type/*.yaml`

### Python Implementation
- **Models**: `endoreg_db/models/requirement/`
- **Evaluation Logic**: `endoreg_db/utils/requirement_operator_logic/`
- **RequirementLinks**: `endoreg_db/utils/links/requirement_link.py`
- **Tests**: `tests/requirement/`

### Loading Data
YAML data is loaded via Django fixtures using the `load_data()` function in test helpers.

## Best Practices

1. **Naming Convention**: Use descriptive, hierarchical names (e.g., `colonoscopy_austria_screening_cecum_visualized`)
2. **Documentation**: Always include `description`, `name_de`, `name_en` fields
3. **Modularity**: Create reusable requirement components that can be combined in different sets
4. **Testing**: Write comprehensive tests for each requirement and requirement set
5. **Validation**: Use the Django admin or management commands to validate YAML structure before deployment
6. **Versioning**: Consider versioning requirement definitions when making breaking changes

This comprehensive guide should help you understand, use, and extend the Requirement Evaluation System effectively. The modular design allows for complex medical logic while maintaining readability and testability.
