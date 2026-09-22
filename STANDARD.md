The repository distinguishes reusable terminology definitions from patient-specific records. This separation allows terminology to be modeled independently of an individual documentation case and then referenced by concrete examinations and findings.

Examinations are examination types defined in the terminology layer. They specify which finding types, indications, and documentation rules are permitted or required in a given context. In the instance layer, a `PatientExamination` represents a concrete examination performed on a patient. Its related patient-specific objects belong to that examination and must be validated against the knowledge base.

Findings are finding definitions that may occur in particular examination types. They specify which classifications and interventions may be associated with them. In the instance layer, a `PatientFinding` is a patient-specific observation or biological entity documented during a concrete examination.

Classifications are structured descriptive dimensions of a finding, such as location, morphology, structure, or other clinical characteristics. A `FindingClassification` may have one or more `FindingClassificationType` values and one or more allowed `FindingClassificationChoice` values. In the instance layer, `PatientFindingClassification` links a concrete patient finding to one classification and one selected choice and validates that choice against the classification.

Classification types identify the kind of classification, such as location, morphology, laboratory value, or laboratory reference. They provide context for how a measurement or observation is recorded.

Classification choices are the permitted values of a classification. Their terminology-layer definitions may represent nominal, ordinal, binary, subcategory, or numerical-descriptor data; patient-specific selections are validated when saved.

The legacy `Requirement*` validator models are no longer part of the production model. Current validation is implemented at typed schema, model, service, and API boundaries; each active workflow must document its own concrete validation contract.

Interventions are terminology-layer intervention definitions. In the instance layer, a `PatientFindingIntervention` links a concrete intervention to a patient finding and can record state and optional start, end, or calendar dates.

`Unit` stores a named measurement unit with an optional description and abbreviation. Patient laboratory values and other measurement-bearing models reference it where defined by their current model contracts.
