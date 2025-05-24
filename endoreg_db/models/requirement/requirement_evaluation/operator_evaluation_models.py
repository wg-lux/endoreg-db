from endoreg_db.models import (
    PatientExamination
)

#TODO this should be a list of all models that are used in the requirement evaluation
#TODO All those models must have a "get_requirement_links" property
operator_evaluation_models = [
    PatientExamination,
]