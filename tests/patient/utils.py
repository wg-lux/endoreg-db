from endoreg_db.models import (
    Patient, PatientExamination
)

from datetime import datetime, timedelta
import random

from typing import Tuple, List

from .conf import (
    TEST_CENTER_NAME,
    TEST_EXAMINATION_NAME_STRINGS
)

# Utility Functions
def create_test_patient(center_name:str=TEST_CENTER_NAME)->Patient:
    patient = Patient.create_generic(center=TEST_CENTER_NAME)

    return patient

def create_patient_with_colonoscopy(center_name:str=TEST_CENTER_NAME)->Tuple[Patient,PatientExamination]:
    patient = Patient.create_generic(center=center_name)

    patient_examination = patient.create_examination(
        examination_name_str="colonoscopy"
    )

    return patient, patient_examination


def create_patient_with_endoscopies(center_name:str=TEST_CENTER_NAME)->Tuple[Patient,List[PatientExamination]]:
    patient = Patient.create_generic(center=center_name)
    patient_examinations = []

    for examination_name_str in TEST_EXAMINATION_NAME_STRINGS:
        # create random start datetime for examination (today - 5 years)
        
        start_date = datetime.now()

        # randomly subtract 365 days * $RANDOM_NUMBER_BETWEEN_0_AND_5
        start_date = start_date - timedelta(days=365 * random.randint(0,5))


        patient_examination = patient.create_examination(
            examination_name_str=examination_name_str,
            date_start=start_date
        )

        patient_examinations.append(patient_examination)
    
    return patient, patient_examinations