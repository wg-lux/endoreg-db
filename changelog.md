# Boundaries were enforced to reduce errors from mixed responsibilities

Persistence, service, and data-model layers were separated where practical. This follows Django's preferred architecture and prevents new model instances from pulling in circular dependencies through services. Validation was strengthened during this work, significantly reducing API error rates.

The newest feature addition, hub transfers, depends on clean validation and anonymization states as well as an existing mutual Transport Layer Security (mTLS) configuration. This remains the largest production blocker.

Report resolution now supports various input knowledge bases.

# Cleaning up API views, moving towards local production use.

In the recent months, tests were cleaned out from most old versions, while ci/cd was reworked by enforcing mypy, ruff and pytest to run locally.


# Reorganizing model Relationships

## Examination
- Many to Many relationship between Examination and ExaminationIndication is now defined in the Examination Model
- Many to Many relationship between Examination and ExaminationTime has been added to the Examination Model
- Many to Many relationship between Examination and Finding has been added to the Examination Model


## Finding
- Many to Many relationship between Finding and FindingClassification is now defined in the Finding Model
- Many to Many relationship between Finding and Examination is now defined in the Examination Model

## FindingClassification
- Removed the Many to Many relationship between FindingClassification and Examination
    - replaced by property which uses findings to retrieve related examinations

## Dataloader
- changed load order of base db data to accommodate new relationships
    - ExaminationIndication data is now loaded before Examination data
    - Finding data is now loaded before Examination data and before ExaminationIndication (depends on FindingInterventions) data

# To do
- Implement PatientExaminationTime model and relationship
    - removed start / end time here as they are properties of the patient related object
- ExaminationTimeType not properly used right now
- ExaminationType not properly used right now
- FindingInterventionType not properly used right now
- FindingClassificationType not properly used right now
- add findingclassifications to finding types?
- add finding: endoscopy_stent generic and classification endoscopy_stent_type 
- add findingIntervention stent removal
