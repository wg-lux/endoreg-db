from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol, Sequence, cast

from pydantic import BaseModel, Field

from endoreg_db.models.medical.disease import Disease, DiseaseClassificationChoice
from endoreg_db.models.medical.event import Event
from endoreg_db.models.medical.examination.examination import Examination
from endoreg_db.models.medical.examination.examination_indication import (
    ExaminationIndication,
    ExaminationIndicationClassificationChoice,
)
from endoreg_db.models.medical.finding.finding import Finding
from endoreg_db.models.medical.finding.finding_classification import (
    FindingClassification,
    FindingClassificationChoice,
)
from endoreg_db.models.medical.finding.finding_intervention import (
    FindingIntervention,
)
from endoreg_db.models.medical.laboratory.lab_value import LabValue
from endoreg_db.models.medical.patient.patient_disease import PatientDisease
from endoreg_db.models.medical.patient.patient_event import PatientEvent
from endoreg_db.models.medical.patient.patient_examination import PatientExamination
from endoreg_db.models.medical.patient.patient_examination_indication import (
    PatientExaminationIndication,
)
from endoreg_db.models.medical.patient.patient_finding import PatientFinding
from endoreg_db.models.medical.patient.patient_lab_sample import (
    PatientLabSample,
    PatientLabSampleType,
)
from endoreg_db.models.medical.patient.patient_lab_value import PatientLabValue
from endoreg_db.models.medical.medication.medication import Medication
from endoreg_db.models.medical.medication.medication_indication import (
    MedicationIndication,
)
from endoreg_db.models.medical.medication.medication_intake_time import (
    MedicationIntakeTime,
)
from endoreg_db.models.medical.medication.medication_schedule import (
    MedicationSchedule,
)
from endoreg_db.models.medical.patient.patient_medication import PatientMedication
from endoreg_db.models.medical.patient.patient_medication_schedule import (
    PatientMedicationSchedule,
)

if TYPE_CHECKING:  # Added for Patient import
    from endoreg_db.models.administration.person.patient.patient import Patient


class _PatientLike(Protocol):
    patient: Patient


class _PatientSampleLike(Protocol):
    sample: _PatientLike


def _empty_examinations() -> list[Examination]:
    return []


def _empty_examination_indications() -> list[ExaminationIndication]:
    return []


def _empty_examination_indication_classification_choices() -> list[
    ExaminationIndicationClassificationChoice
]:
    return []


def _empty_patient_examinations() -> list[PatientExamination]:
    return []


def _empty_patient_examination_indications() -> list[PatientExaminationIndication]:
    return []


def _empty_lab_values() -> list[LabValue]:
    return []


def _empty_patient_lab_values() -> list[PatientLabValue]:
    return []


def _empty_patient_lab_samples() -> list[PatientLabSample]:
    return []


def _empty_patient_diseases() -> list[PatientDisease]:
    return []


def _empty_diseases() -> list[Disease]:
    return []


def _empty_disease_classification_choices() -> list[DiseaseClassificationChoice]:
    return []


def _empty_events() -> list[Event]:
    return []


def _empty_patient_events() -> list[PatientEvent]:
    return []


def _empty_patient_findings() -> list[PatientFinding]:
    return []


def _empty_findings() -> list[Finding]:
    return []


def _empty_finding_classification_choices() -> list[FindingClassificationChoice]:
    return []


def _empty_finding_classifications() -> list[FindingClassification]:
    return []


def _empty_finding_interventions() -> list[FindingIntervention]:
    return []


def _empty_patient_lab_sample_types() -> list[PatientLabSampleType]:
    return []


def _empty_patient_medications() -> list[PatientMedication]:
    return []


def _empty_patient_medication_schedules() -> list[PatientMedicationSchedule]:
    return []


def _empty_medications() -> list[Medication]:
    return []


def _empty_medication_indications() -> list[MedicationIndication]:
    return []


def _empty_medication_intake_times() -> list[MedicationIntakeTime]:
    return []


def _empty_medication_schedules() -> list[MedicationSchedule]:
    return []


class ModelLinks(BaseModel):
    """
    A collection of linked domain models gathered from a clinical object graph.

    Attributes:
        examinations (List[Examination]): A List of examinations.
        examination_indications (List[ExaminationIndication]): A List of examination indications.
        lab_values (List[LabValue]): A List of lab values.
        diseases (List[Disease]): A List of diseases.
        disease_classification_choices (List[DiseaseClassificationChoice]): A List of disease classification choices.
        events (List[Event]): A List of events.
        findings (List[Finding]): A List of findings.
        finding_morphology_classification_choices (List[FindingMorphologyClassificationChoice]): A List of finding morphology classification choices.
        finding_location_classification_choices (List[FindingLocationClassificationChoice]): A List of finding location classification choices.
        finding_interventions (List[FindingIntervention]): A List of finding interventions.
    """

    model_config = {"arbitrary_types_allowed": True}
    examinations: list[Any] = Field(default_factory=_empty_examinations)
    examination_indications: list[Any] = Field(
        default_factory=_empty_examination_indications
    )
    examination_indication_classification_choices: list[Any] = Field(
        default_factory=_empty_examination_indication_classification_choices
    )
    patient_examinations: list[Any] = Field(default_factory=_empty_patient_examinations)

    patient_examination_indication: list[Any] = Field(
        default_factory=_empty_patient_examination_indications
    )
    lab_values: list[Any] = Field(default_factory=_empty_lab_values)
    patient_lab_values: list[Any] = Field(default_factory=_empty_patient_lab_values)
    patient_lab_samples: list[Any] = Field(default_factory=_empty_patient_lab_samples)
    patient_diseases: list[Any] = Field(default_factory=_empty_patient_diseases)
    diseases: list[Any] = Field(default_factory=_empty_diseases)
    disease_classification_choices: list[Any] = Field(
        default_factory=_empty_disease_classification_choices
    )
    events: list[Any] = Field(default_factory=_empty_events)
    patient_events: list[Any] = Field(default_factory=_empty_patient_events)
    patient_findings: list[Any] = Field(default_factory=_empty_patient_findings)
    findings: list[Any] = Field(default_factory=_empty_findings)
    finding_classification_choices: list[Any] = Field(
        default_factory=_empty_finding_classification_choices
    )
    finding_classifications: list[Any] = Field(
        default_factory=_empty_finding_classifications
    )
    finding_interventions: list[Any] = Field(
        default_factory=_empty_finding_interventions
    )
    patient_lab_sample_types: list[Any] = Field(
        default_factory=_empty_patient_lab_sample_types
    )
    patient_medications: list[Any] = Field(default_factory=_empty_patient_medications)
    patient_medication_schedules: list[Any] = Field(
        default_factory=_empty_patient_medication_schedules
    )
    # Added direct medication-related fields
    medications: list[Any] = Field(default_factory=_empty_medications)
    medication_indications: list[Any] = Field(
        default_factory=_empty_medication_indications
    )
    medication_intake_times: list[Any] = Field(
        default_factory=_empty_medication_intake_times
    )
    medication_schedules: list[Any] = Field(default_factory=_empty_medication_schedules)

    def get_first_patient(self) -> Patient | None:
        """
        Retrieves the first Patient instance found through the linked patient-specific models.
        Iterates through various patient-related lists and returns the .patient attribute
        from the first relevant object found.
        """
        if self.patient_lab_values:
            for plv in self.patient_lab_values:
                sample = cast(_PatientSampleLike, plv).sample
                if sample.patient:
                    return sample.patient
        if self.patient_lab_samples:
            for pls in self.patient_lab_samples:
                patient_like = cast(_PatientLike, pls)
                if patient_like.patient:
                    return patient_like.patient
        if self.patient_examinations:
            for pe in self.patient_examinations:
                patient_like = cast(_PatientLike, pe)
                if patient_like.patient:
                    return patient_like.patient
        if self.patient_diseases:
            for pd in self.patient_diseases:
                patient_like = cast(_PatientLike, pd)
                if patient_like.patient:
                    return patient_like.patient
        if self.patient_events:
            for pev in self.patient_events:
                patient_like = cast(_PatientLike, pev)
                if patient_like.patient:
                    return patient_like.patient
        if self.patient_findings:
            for pf in self.patient_findings:
                patient_like = cast(_PatientLike, pf)
                if patient_like.patient:
                    return patient_like.patient
        # Check PatientMedication
        if self.patient_medications:
            for pm in self.patient_medications:
                patient_like = cast(_PatientLike, pm)
                if patient_like.patient:
                    return patient_like.patient
        # Check PatientMedicationSchedule
        if self.patient_medication_schedules:
            for pms in self.patient_medication_schedules:
                patient_like = cast(_PatientLike, pms)
                if patient_like.patient:
                    return patient_like.patient
        return None

    def match_any(self, other: "ModelLinks") -> bool:
        """
        Determines if any linked model in this instance is also present in another ModelLinks instance.

        Compares each list attribute of both instances and returns True if any element in any list overlaps.
        """

        other_dict = other.model_dump()
        self_dict = self.model_dump()
        for key in self_dict:
            # print(f"Checking key: {key}") # This is a debug print, can be removed
            if key in other_dict and self_dict[key] and other_dict[key]:
                if any(item in other_dict[key] for item in self_dict[key]):
                    return True
        return False  # Ensure False is returned if no match is found

    def active(self) -> dict[str, Sequence[object]]:
        """
        Returns a dictionary of all non-empty linked model lists.

        Only attributes with non-empty lists are included in the returned dictionary.
        """
        active_links_dict: dict[str, Sequence[object]] = {}
        # Use model_dump() to iterate field data reliably (pydantic v2)
        for field_name, field_value in self.model_dump().items():
            if isinstance(field_value, list) and field_value:
                active_links_dict[field_name] = cast(Sequence[object], field_value)
        return active_links_dict

    def __repr__(self):
        """
        Returns a concise string summarizing the counts of each linked model list in the instance.
        """
        data = self.model_dump()
        fields = [
            "examinations",
            "examination_indications",
            "patient_examinations",
            "lab_values",
            "patient_lab_values",
            "patient_diseases",
            "diseases",
            "disease_classification_choices",
            "events",
            "patient_events",
            "findings",
            "patient_findings",
            "finding_classification_choices",
            "finding_interventions",
            "patient_medications",
            "patient_medication_schedules",
            "medications",
            "medication_indications",
            "medication_intake_times",
            "medication_schedules",
        ]
        parts = [f"{f}={len(data.get(f, []))}" for f in fields]
        return f"ModelLinks({', '.join(parts)})"
