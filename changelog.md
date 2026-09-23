# Boundaries were enforced to reduce errors from mixed responsibilities

Persistence, service, and data-model layers were separated where practical. This reduces circular dependencies between models and services. Validation boundaries were strengthened during this work.

Hub transfer is production-critical. Remaining readiness work includes deployed envelope-encryption and key-rotation verification, plus production-like recovery, cleanup, and capacity exercises. The authoritative status is maintained in `feature-tracking/HubTransfer.yml`.

Report resolution now supports various input knowledge bases.

# API view and local-production improvements

Continuous integration currently runs Ruff, mypy, repository smoke checks, and the fast pytest lane. Local pre-commit checks also run Pyright and repository contract checks.


# Model relationship reorganization

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

Current implementation and production-readiness work is tracked only in `feature-tracking/*.yml`; this changelog does not maintain a parallel to-do list.
