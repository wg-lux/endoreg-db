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