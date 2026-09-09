## Patient Model Manual

The `Patient` model is a cornerstone of the system, representing individuals receiving medical care. It inherits from a more general `Person` model, likely adding patient-specific attributes and relationships.

### Core Patient Information

*   A `Patient` has standard demographic information inherited from `Person` (e.g., name, date of birth, gender).
*   It can be associated with a `Center` where care is provided.
*   The model includes methods for managing patient identity, such as `get_or_create_pseudo_patient_by_hash` for anonymized or external data integration, and calculating age (`age()`, `get_dob()`).

### 1. Patient Examinations and Findings

Examinations are central to documenting patient encounters.

*   **`PatientExamination`**:
    *   This model links a `Patient` to a specific `Examination` (e.g., colonoscopy, gastroscopy) and records details like the start and end dates of the examination.
    *   Each `PatientExamination` has a unique `hash`.
    *   It can be associated with zero or more `VideoFile` records through the `video_files` reverse relation.
    *   It serves as the primary link for all findings, indications, and reports related to that specific examination instance for the patient.
    *   A `PatientExamination` can be created with `patient.create_examination(...)` or through the applicable import or service workflow.

*   **`PatientFinding`**:
    *   This model represents a specific clinical observation or `Finding` (e.g., "polyp," "inflammation") identified during a `PatientExamination`.
    *   It directly links a `PatientExamination` to a `Finding` definition.
    *   The `PatientFinding` model is crucial as it connects the general observation to its specific characteristics for that patient, such as its location and morphology.
    *   A `PatientExamination` can have multiple `PatientFinding` records associated with it via the `patient_findings` related name. You can retrieve these using `patient_examination_instance.get_findings()`.

*   **Classifying Findings (`PatientFindingClassification`)**:
    *   `PatientFindingClassification` links a `PatientFinding` to a `FindingClassification` and a `FindingClassificationChoice`.
    *   Location and morphology are represented through the applicable finding classifications rather than separate patient-location or patient-morphology models.
    *   On save, the model validates that the selected choice belongs to the classification and initializes and validates typed `subcategories` and `numerical_descriptors` payloads.

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
    *   It records a numeric `value` and/or `value_str`, an optional `unit`, and an automatically created `timestamp`.
    *   It can optionally link to a `PatientLabSample` if the lab value was derived from a specific sample.
    *   It includes a `normal_range` (often a JSON field like `{"min": X, "max": Y}`) which can be determined by considering the `LabValue` type, patient's age, and gender using the `get_normal_range()` method.
    *   The `Patient` model exposes the `lab_values` reverse relation for accessing its `PatientLabValue` instances.

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

### Linked-model traversal (`.links` property)

Several patient-specific models, including `Patient`, `PatientMedication`, `PatientEvent`, `PatientDisease`, and `PatientLabValue`, expose a `.links` property. It returns a typed `ModelLinks` object that aggregates related domain-model instances for linked-model traversal. `PatientExamination` does not currently expose this property.
