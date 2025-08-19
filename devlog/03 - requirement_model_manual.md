## Requirement Validation System Manual

This document details the sophisticated system used for validating conditions, particularly concerning patient-related data. It leverages `Requirement` objects, `RequirementOperator`s, and `RequirementSet`s (including nested sets) to define and evaluate complex logic.

### Core Concepts

The system is built around a few key components:

1.  **`Requirement`**: Represents a single, specific condition that needs to be checked.
2.  **`RequirementOperator`**: Defines *how* a `Requirement` is evaluated against an input object.
3.  **`RequirementSet`**: Groups multiple `Requirement`s and/or other `RequirementSet`s to represent more complex logical conditions (e.g., Condition A AND (Condition B OR Condition C)).
4.  **Input Objects**: These are typically Django model instances (e.g., `Patient`, `PatientMedication`, `PatientLabValue`, `PatientExaminationIndication`) against which requirements are evaluated.
5.  **`.links` Property**: A crucial property found on many models (including `Patient` and `Requirement` itself). It returns a `RequirementLinks` object, which aggregates various related model instances and data points relevant for evaluation.

### 1. The `Requirement` Model

A `Requirement` object is the fundamental unit of a condition.

*   **Purpose**: To define a specific criterion to be met.
*   **Key Fields**:
    *   `name`: A unique identifier for the requirement (e.g., `"patient_has_medication_aspirin"`, `"lab_latest_numeric_increased_hemoglobin"`).
    *   `description`: A human-readable explanation of the requirement.
    *   `requirement_types`: A ManyToManyField to `RequirementType`. This categorizes the requirement and helps determine which data models are relevant for its evaluation (e.g., "patient", "patient_medication", "lab_value"). The `Requirement.expected_models` property often derives from these types.
    *   `operators`: A ManyToManyField to `RequirementOperator`. Specifies one or more operators that define the evaluation logic.
    *   **Data Linking Fields**: Various ForeignKey or ManyToManyFields to other models, specifying the entities the requirement is concerned with. Examples:
        *   `medications` (to `Medication` model)
        *   `diseases` (to `Disease` model)
        *   `lab_values` (to `LabValue` model)
        *   `events` (to `Event` model)
        *   `finding_interventions` (to `FindingIntervention` model)
        *   `examination_indications` (to `ExaminationIndication` model)
    *   **Condition-Defining Fields**:
        *   `numeric_value`, `numeric_value_min`, `numeric_value_max`: For conditions involving numerical comparisons.
        *   `string_value`: For string matching.
        *   `boolean_value`: For boolean checks.
        *   `unit`: Links to a `Unit` model, essential for timeframe calculations or value comparisons with units.
        *   `timeframe_days` (or similar, often represented by `numeric_value_min`/`max` in conjunction with a `unit` like "days"): For conditions that must be met within a certain period.

### 2. The `RequirementOperator` Model

A `RequirementOperator` defines the actual logic used to evaluate a `Requirement`.

*   **Purpose**: To provide a specific evaluation function.
*   **Key Fields**:
    *   `name`: A unique name for the operator (e.g., `"models_match_any"`, `"lab_latest_numeric_increased"`, `"models_match_any_in_timeframe"`).
    *   `evaluation_function_name`: A string that stores the name of the actual Python method (often within the `RequirementOperator` class itself or a utility module) that performs the evaluation.
*   **How it Works**:
    *   The `RequirementOperator.evaluate(requirement_links: RequirementLinks, input_links: RequirementLinks, **kwargs) -> bool` method is central.
    *   It typically uses the `evaluation_function_name` to dynamically call the specific validation logic.
    *   This validation logic compares data from `requirement_links` (derived from the `Requirement` object itself, e.g., `requirement.medications.all()`) with data from `input_links` (derived from the `input_object` being evaluated, e.g., `patient.links.medications`).
*   **Examples of Operators (inferred from tests)**:
    *   `models_match_any`: Checks if any of the entities specified in the `Requirement` (e.g., specific diseases) are present in the corresponding entities of the input object.
    *   `lab_latest_numeric_increased`: Checks if the latest relevant lab value for a patient has increased (potentially compared to a previous value or a baseline, details depend on the specific validator function).
    *   `lab_latest_numeric_decreased`, `lab_latest_numeric_normal`: Similar to above, but for decreased or normal values.
    *   `lab_latest_numeric_lower_than_value`, `lab_latest_numeric_greater_than_value`: Compares the latest lab value against a `numeric_value` defined in the `Requirement`.
    *   `..._in_timeframe` (e.g., `models_match_any_in_timeframe`, `lab_latest_numeric_increased_factor_in_timeframe`): These operators add a temporal constraint, checking if the condition was met within a timeframe specified by `numeric_value_min`, `numeric_value_max`, and `unit` on the `Requirement`.
    *   `patient_medication_schedule_matches_template`: A specialized operator that might check if a `PatientMedication` instance's details (drug, dose, unit, intake times) align with a predefined `MedicationSchedule` template linked to the `Requirement`.

### 3. The `RequirementSet` Model

`RequirementSet`s allow for the grouping of `Requirement`s and other `RequirementSet`s to build complex logical structures.

*   **Purpose**: To combine multiple conditions.
*   **Key Fields (expected)**:
    *   `name`: A unique identifier for the set.
    *   `requirements`: A ManyToManyField to `Requirement`, linking the individual requirements that are part of this set.
    *   `links_to_sets` (or a similar name like `child_sets`): A ManyToManyField to itself (`'self'`), allowing `RequirementSet`s to be nested within other `RequirementSet`s.
    *   `set_operator_type` (e.g., "AND", "OR"): A field to define how the results of the contained requirements/sets are combined (e.g., all must be true for "AND", at least one for "OR").
*   **Evaluation**:
    *   When a `RequirementSet` is evaluated, it would iterate through its linked `Requirement`s and any nested `RequirementSet`s.
    *   The results of these individual evaluations are then combined based on the `set_operator_type`.
    *   For example, a set with "AND" logic would only be true if all its contained requirements and nested sets evaluate to true.
*   **Nested RequirementSets**:
    *   The `links_to_sets` field is key to creating hierarchies.
    *   This enables the construction of sophisticated rules like:
        *   `(RequirementA AND RequirementB) OR RequirementC`
        *   `RequirementSet_Screening_Eligibility AND (RequirementSet_HighRisk_Factors OR RequirementSet_Specific_Symptoms)`

### 4. The Evaluation Process

The core of the system is the evaluation of an input object against a `Requirement` or `RequirementSet`.

1.  **Initiation**: Evaluation is typically triggered by calling an `evaluate()` method, often on the `Requirement` or `RequirementSet` instance, passing the `input_object`.
    *   Example: `requirement.evaluate(patient_instance, mode="loose")`

2.  **Gathering Context (`.links`)**:
    *   **Input Links**: The `input_object`'s `.links` property is accessed. This `RequirementLinks` object provides a structured way to access all relevant data associated with the input (e.g., for a `Patient` input, `patient.links.diseases`, `patient.links.medications`, `patient.links.lab_values`).
    *   **Requirement Links**: The `Requirement` object itself holds the criteria. Its direct field values (e.g., `numeric_value`) and linked model instances (e.g., `requirement.diseases.all()`) form the "requirement links". These can also be packaged into a `RequirementLinks` object.

3.  **Operator Execution**:
    *   The `Requirement.evaluate()` method iterates through its associated `RequirementOperator`(s).
    *   For each operator, its `evaluate(requirement_links, input_links, **kwargs)` method is called.
    *   The operator's specific validation logic (the function named in `evaluation_function_name`) is executed, comparing the `requirement_links` against the `input_links`.

4.  **Result**: The operator's evaluation function returns `True` or `False`. If a `Requirement` has multiple operators, their results might be combined (e.g., all must be true). The `Requirement.evaluate()` method then returns the final boolean outcome.

5.  **`RequirementSet` Evaluation**:
    *   If evaluating a `RequirementSet`, it will, in turn, call `evaluate()` on each of its contained `Requirement`s and nested `RequirementSet`s, using the same `input_object`.
    *   The individual boolean results are then aggregated based on the set's logic (e.g., AND, OR).

### 5. Practical Examples from Tests

The test suite provides excellent illustrations:

*   **Checking for a Specific Disease (`test_requirement_disease_is.py`)**:
    *   A `Requirement` (e.g., `"disease_is_diabetes"`) links to the "Diabetes" `Disease` object.
    *   When `requirement.evaluate(patient)` is called:
        *   The operator (likely `models_match_any`) checks if "Diabetes" (from `requirement.links.diseases`) is present in `patient.links.diseases`.

*   **Lab Value Checks (`test_requirement_lab_value_latest.py`)**:
    *   A `Requirement` (e.g., `"lab_latest_numeric_increased_hemoglobin"`) links to the "Hemoglobin" `LabValue` and uses the `lab_latest_numeric_increased` operator.
    *   `requirement.evaluate(patient)`: The operator would find the patient's most recent Hemoglobin `PatientLabValue` via `patient.links.lab_values` and apply its logic to determine if it has "increased".
    *   Another example: `"lab_latest_numeric_greater_than_value_creatinine"` might have `numeric_value = 1.5` and link to "Creatinine". The operator checks if `patient.links.latest_creatinine.value > 1.5`.

*   **Timeframe-based Event (`test_requirement_event.py`)**:
    *   A `Requirement` (e.g., `"event_stroke_in_last_30_days"`) links to the "Stroke" `Event`, has `numeric_value_min = -30`, `numeric_value_max = 0`, `unit = "days"`, and uses the `models_match_any_in_timeframe` operator.
    *   `requirement.evaluate(patient)`: The operator checks if `patient.links.events` contains a "Stroke" `PatientEvent` with a `date_start` within the last 30 days.

*   **Medication Schedule Matching (`test_requirement_medication.py`)**:
    *   A `Requirement` (e.g., `"patient_schedule_contains_apixaban_5mg_bid_profile"`) links to a `MedicationSchedule` template (e.g., "apixaban-5mg-twice_daily").
    *   `requirement.evaluate(patient)` or `requirement.evaluate(patient_medication_schedule_instance)`: The operator (e.g., `_evaluate_patient_medication_schedule_matches_template`) iterates through the patient's `PatientMedication` instances (from `patient.links.patient_medications` or directly from the `PatientMedicationSchedule` input) and checks if any of them match the drug, dosage, unit, and intake times defined in the "apixaban-5mg-twice_daily" `MedicationSchedule` template linked to the requirement.

### 6. Validator Functions

The `evaluation_function_name` field in the `RequirementOperator` model points to the specific Python function that contains the detailed logic for that operator. These functions are the heart of the validation, performing the actual comparisons and calculations based on the data extracted from the `requirement_links` and `input_links`.

This system provides a highly flexible and extensible way to define and evaluate a wide array of conditions against complex data models, making it a powerful tool for implementing clinical guidelines, data validation rules, or decision support logic.
