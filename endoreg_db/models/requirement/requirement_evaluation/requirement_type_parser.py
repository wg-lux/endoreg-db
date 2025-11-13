"""
Validated registry of requirement-type model bindings.

The registry ensures the string keys stay aligned with the Django models while
providing structured validation via Pydantic. Downstream code should consume
``data_model_dict``/``data_model_dict_reverse`` instead of re-declaring
unvalidated mappings.
"""

from typing import Dict

from django.db import models
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from endoreg_db.models import (
    Disease,
    DiseaseClassification,
    DiseaseClassificationChoice,
    Event,
    EventClassification,
    EventClassificationChoice,
    Examination,
    ExaminationIndication,
    Finding,
    FindingClassification,
    FindingClassificationChoice,
    FindingIntervention,
    LabValue,
    Patient,
    PatientDisease,
    PatientEvent,
    PatientExamination,
    PatientFinding,
    PatientFindingClassification,
    PatientFindingIntervention,
    PatientLabSample,
    PatientLabValue,
    PatientMedication,
    PatientMedicationSchedule,
)
from endoreg_db.models.other.gender import Gender

# if TYPE_CHECKING:
#     from endoreg_db.models import (
#         RequirementOperator,
#         Patient,
#     )


class DataModelEntry(BaseModel):
    """Validated binding between an identifier string and a Django model class."""

    name: str = Field(min_length=1)
    model: type[models.Model]

    model_config = ConfigDict(arbitrary_types_allowed=True)

    @field_validator("model")
    @classmethod
    def ensure_model_subclass(cls, value: type[models.Model]) -> type[models.Model]:
        if not issubclass(value, models.Model):  # Defensive: ensure provided class is a Django model
            raise TypeError(f"{value!r} is not a Django model class")
        return value


class DataModelRegistry(BaseModel):
    """Collection of ``DataModelEntry`` items with duplicate safeguards."""

    entries: tuple[DataModelEntry, ...]

    model_config = ConfigDict(arbitrary_types_allowed=True)

    @model_validator(mode="after")
    def ensure_unique(self) -> "DataModelRegistry":
        names = [entry.name for entry in self.entries]
        models_set = [entry.model for entry in self.entries]
        if len(names) != len(set(names)):
            raise ValueError("Duplicate requirement type names detected")
        if len(models_set) != len(set(models_set)):
            raise ValueError("Duplicate Django model classes detected in registry")
        return self

    def as_mapping(self) -> Dict[str, type[models.Model]]:
        return {entry.name: entry.model for entry in self.entries}

    def as_reverse_mapping(self) -> Dict[type[models.Model], str]:
        return {entry.model: entry.name for entry in self.entries}


DATA_MODEL_REGISTRY = DataModelRegistry(
    entries=(
        DataModelEntry(name="disease", model=Disease),
        DataModelEntry(name="disease_classification_choice", model=DiseaseClassificationChoice),
        DataModelEntry(name="disease_classification", model=DiseaseClassification),
        DataModelEntry(name="event", model=Event),
        DataModelEntry(name="event_classification", model=EventClassification),
        DataModelEntry(name="event_classification_choice", model=EventClassificationChoice),
        DataModelEntry(name="examination", model=Examination),
        DataModelEntry(name="examination_indication", model=ExaminationIndication),
        DataModelEntry(name="finding", model=Finding),
        DataModelEntry(name="finding_intervention", model=FindingIntervention),
        DataModelEntry(name="finding_classification", model=FindingClassification),
        DataModelEntry(name="finding_classification_choice", model=FindingClassificationChoice),
        DataModelEntry(name="lab_value", model=LabValue),
        DataModelEntry(name="patient_disease", model=PatientDisease),
        DataModelEntry(name="patient_event", model=PatientEvent),
        DataModelEntry(name="patient_examination", model=PatientExamination),
        DataModelEntry(name="patient_finding", model=PatientFinding),
        DataModelEntry(name="patient_finding_intervention", model=PatientFindingIntervention),
        DataModelEntry(name="patient_finding_classification", model=PatientFindingClassification),
        DataModelEntry(name="patient_lab_value", model=PatientLabValue),
        DataModelEntry(name="patient_lab_sample", model=PatientLabSample),
        DataModelEntry(name="patient", model=Patient),
        DataModelEntry(name="patient_medication", model=PatientMedication),
        DataModelEntry(name="patient_medication_schedule", model=PatientMedicationSchedule),
        DataModelEntry(name="gender", model=Gender),
    ),
)

data_model_dict: Dict[str, type[models.Model]] = DATA_MODEL_REGISTRY.as_mapping()
data_model_dict_reverse: Dict[type[models.Model], str] = DATA_MODEL_REGISTRY.as_reverse_mapping()
