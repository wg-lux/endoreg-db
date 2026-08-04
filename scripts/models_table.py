from django_setup import setup_django

setup_django()

from endoreg_db.models import (
    Examination,
    ExaminationIndication,
    Finding, 
    FindingIntervention, 
    FindingClassification,
    FindingClassificationChoice,
    FindingClassificationType,
    Disease, DiseaseClassification,
    Event, EventClassification,
    Medication, MedicationIndication,

)

import pandas as pd

export_models = [    Examination,
    ExaminationIndication,
    Finding, 
    FindingIntervention, 
    FindingClassification, FindingClassificationChoice,
    FindingClassificationType,
    Disease, DiseaseClassification,
    Event, EventClassification,
    Medication, MedicationIndication
]

data_dict = {
    model.__name__: [] for model in export_models
}

for model in export_models:
    # Get all objects of the model
    objects = model.objects.all()

    for obj in objects:
        data_dict[model.__name__].append(obj.name)
    
# Create a data frame with columns ModelName and ObjectName
df_dict = {"ModelName": [], "ObjectName": []}
for model_name, object_names in data_dict.items():
    for object_name in object_names:
        df_dict["ModelName"].append(model_name)
        df_dict["ObjectName"].append(object_name)

df = pd.DataFrame(df_dict)
# Export Data so we can copy paste to google docs (tab delimited)
df.to_csv("models_table.tsv", sep="\t", index=False, header=False)
