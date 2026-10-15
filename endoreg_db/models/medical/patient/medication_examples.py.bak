from django.utils.functional import SimpleLazyObject

from endoreg_db.models import Medication, MedicationIndication, MedicationIntakeTime
from endoreg_db.helpers.default_objects import generate_patient


def _available_medications():
    return Medication.objects.all()


def _common_intake_times():
    return MedicationIntakeTime.objects.filter(
        name__in=["daily-morning", "daily-noon", "daily-evening", "daily-night"]
    )


def _daily_morning():
    return MedicationIntakeTime.dm()


def _daily_noon():
    return MedicationIntakeTime.dno()


def _daily_evening():
    return MedicationIntakeTime.de()


def _daily_night():
    return MedicationIntakeTime.dn()


def _random_medication_indication():
    return MedicationIndication.objects.order_by("?").first()


def _example_patient():
    return generate_patient()


available_medications = SimpleLazyObject(_available_medications)
common_intake_times = SimpleLazyObject(_common_intake_times)
daily_morning = SimpleLazyObject(_daily_morning)
daily_noon = SimpleLazyObject(_daily_noon)
daily_evening = SimpleLazyObject(_daily_evening)
daily_night = SimpleLazyObject(_daily_night)
mi = SimpleLazyObject(_random_medication_indication)

# Alternatively, we can use a specific indication type

# FIXME MedicationIndicationType does not have a field named indication_type
# It does have: id, medication_indications, name

# medication_indication_type = MedicationIndicationType.objects.filter(indication_type='thromboembolism-prevention-non_valvular_af').order_by('?').first()

patient = SimpleLazyObject(_example_patient)
