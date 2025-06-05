## Patient Model Manual

The `Patient` model is a cornerstone of the system, representing individuals receiving medical care. It inherits from a more general `Person` model, likely adding patient-specific attributes and relationships.

### Core Patient Information

*   A `Patient` has standard demographic information inherited from `Person` (e.g., name, date of birth, gender).
*   It's associated with a `Center` where they might be receiving care.
*   The model includes methods for managing patient identity, such as `get_or_create_pseudo_patient_by_hash` for anonymized or external data integration, and calculating age (`age()`, `get_dob()`).

### 1. Patient Examinations and Findings

Examinations are central to documenting patient encounters.

*   **`PatientExamination`**:
    *   This model links a `Patient` to a specific `Examination` (e.g., colonoscopy, gastroscopy) and records details like the start and end dates of the examination.
    *   Each `PatientExamination` has a unique `hash`.
    *   It can be associated with a `VideoFile` if the examination was recorded.
    *   It serves as the primary link for all findings, indications, and reports related to that specific examination instance for the patient.
    *   You can create `PatientExamination` instances for a patient, for example, by using `patient.create_examination_by_indication()`.

*   **`PatientFinding`**:
    *   This model represents a specific clinical observation or `Finding` (e.g., "polyp," "inflammation") identified during a `PatientExamination`.
    *   It directly links a `PatientExamination` to a `Finding` definition.
    *   The `PatientFinding` model is crucial as it connects the general observation to its specific characteristics for that patient, such as its location and morphology.
    *   A `PatientExamination` can have multiple `PatientFinding` records associated with it via the `patient_findings` related name. You can retrieve these using `patient_examination_instance.get_findings()`.

*   **Connecting Findings to Locations (`PatientFindingLocation`)**:
    *   The `PatientFindingLocation` model specifies where a particular `PatientFinding` was observed.
    *   It links back to a `PatientFinding` instance (via the `finding` foreign key, accessible from `PatientFinding` through the `locations` related name).
    *   It uses `FindingLocationClassification` (e.g., "Colon Segment") and `FindingLocationClassificationChoice` (e.g., "Sigmoid Colon") to define the location precisely.
    *   It can also store `subcategories` and `numerical_descriptors` (e.g., distance from a landmark) as JSON data, which are often initialized from the chosen `FindingLocationClassificationChoice`.
    *   The `save()` method on `PatientFindingLocation` includes logic to ensure the `location_choice` is valid for the `location_classification` and to populate `subcategories` and `numerical_descriptors` if they are not already set.

*   **Connecting Findings to Morphologies (`PatientFindingMorphology`)**:
    *   The `PatientFindingMorphology` model describes the form and structure (morphology) of a `PatientFinding`.
    *   It links back to a `PatientFinding` instance (accessible from `PatientFinding` through the `morphologies` related name).
    *   It uses `FindingMorphologyClassification` (e.g., "Paris Classification") and `FindingMorphologyClassificationChoice` (e.g., "Paris 0-Ip") to detail the morphology.
    *   Similar to locations, it can store `subcategories` and `numerical_descriptors` related to the morphology.

*   **Interventions for Findings (`PatientFindingIntervention`)**:
    *   If an intervention (e.g., "biopsy," "polypectomy") is performed related to a specific `PatientFinding`, it's recorded in the `PatientFindingIntervention` model.
    *   This model links a `PatientFinding` to a `FindingIntervention` definition and can include details like the state and timing of the intervention.

### 2. Patient Medications

The system tracks medications a patient is taking.

*   **`PatientMedication`**:
    *   This is the core model for an individual medication instance for a patient.
    *   It links directly to the `Patient`.
    *   It specifies the `Medication` (the drug itself, e.g., "Aspirin").
    *   It can link to a `MedicationIndication` (the reason for taking the medication, e.g., "Thromboembolism Prevention").
    *   It stores `dosage` (as a JSON field, allowing for flexible dosage information), the `Unit` of the dosage.
    *   It has a many-to-many relationship with `MedicationIntakeTime` to record when the medication is taken (e.g., "daily-morning," "daily-evening").
    *   An `active` boolean field indicates if the medication is currently being taken.
    *   The `Patient` model has a reverse relation `patientmedication_set` to access all `PatientMedication` instances.

*   **`PatientMedicationSchedule`**:
    *   This model groups multiple `PatientMedication` instances to represent a patient's overall medication regimen.
    *   It links directly to a `Patient`.
    *   It has a many-to-many relationship with `PatientMedication` (via the `medication` field).
    *   This allows for organizing complex medication plans. For example, a patient might have a "Morning Medications" schedule and an "Evening Medications" schedule, each containing several `PatientMedication` entries.
    *   Class methods like `create_by_patient_and_indication_type` help in creating schedules with initial medications based on indications.

### 3. Patient Lab Values

Laboratory results are managed through the following:

*   **`PatientLabValue`**:
    *   This model stores a specific laboratory test result for a `Patient`.
    *   It links to the `Patient`.
    *   It links to a `LabValue` model, which defines the type of lab test (e.g., "Hemoglobin," "Creatinine").
    *   It records the `value` of the test, the `unit`, and the `datetime` the sample was taken or the result was recorded.
    *   It can optionally link to a `PatientLabSample` if the lab value was derived from a specific sample.
    *   It includes a `normal_range` (often a JSON field like `{"min": X, "max": Y}`) which can be determined by considering the `LabValue` type, patient's age, and gender using the `get_normal_range()` method.
    *   The `Patient` model has a reverse relation `lab_values` (likely `patientlabvalue_set` or a custom related name) to access all `PatientLabValue` instances.

*   **`PatientLabSample`**:
    *   This model represents a physical sample taken from a patient (e.g., "blood sample," "urine sample").
    *   It links to the `Patient` and a `PatientLabSampleType`.
    *   It records the `date` the sample was taken.
    *   Multiple `PatientLabValue` records can be associated with a single `PatientLabSample`.

### 4. Patient Events

General health events or occurrences outside of formal examinations are tracked using:

*   **`PatientEvent`**:
    *   This model records specific health-related events for a `Patient`. These could be symptoms, adverse reactions, or procedures not part of a structured `PatientExamination`.
    *   It links to the `Patient` and an `Event` (which defines the type of event, e.g., "Nausea," "Follow-up Consultation").
    *   It includes `date_start` and optionally `date_end` for the event's duration.
    *   It can link to an `EventClassificationChoice` for more detailed categorization of the event.
    *   It can store `subcategories` and `numerical_descriptors` as JSON to further describe the event.
    *   The `Patient` model has a reverse relation `events` (likely `patientevent_set`) to access all `PatientEvent` instances.

### 5. Patient Diseases

Diagnosed diseases are recorded as follows:

*   **`PatientDisease`**:
    *   This model represents a disease that a `Patient` has been diagnosed with.
    *   It links to the `Patient` and a `Disease` (which defines the type of disease, e.g., "Crohn's Disease," "Hypertension").
    *   It can have `start_date` and `end_date` to track the duration of the disease if applicable.
    *   It has a many-to-many relationship with `DiseaseClassificationChoice` to add more specific classifications to the diagnosis (e.g., severity, stage).
    *   It can store `numerical_descriptors` and `subcategories` as JSON for additional details about the patient's specific instance of the disease.
    *   The `Patient` model has a reverse relation `diseases` (likely `patientdisease_set`) to access all `PatientDisease` instances.

### Linking for Requirement Evaluation (`.links` property)

Many of these patient-specific models (e.g., `Patient`, `PatientExamination`, `PatientMedication`, `PatientEvent`, `PatientDisease`, `PatientLabValue`) have a `.links` property. This property returns a `RequirementLinks` object, which aggregates various related model instances. This is a utility designed for a "requirements evaluation" system, allowing complex rules or criteria to be checked against the patient's comprehensive data. For instance, a requirement might check if a patient with a certain disease is on a specific medication and has a recent lab value within a particular range.

