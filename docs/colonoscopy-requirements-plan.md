# Colonoscopy Requirements Context

> Status tracking lives in `feature-tracking/Colonoscopy.yml`. This historical
> requirements page does not carry an independent completion status.

The intended report content includes:

- unambiguous patient identification, including name, date of birth, and
  patient ID where the applicable privacy boundary permits it;
- the clinical indication for endoscopy;
- the responsible examiner and assisting staff;
- the endoscope type and unique device identifier;
- examination start and end date and time;
- bowel preparation, for example the Boston Bowel Preparation Scale (BBPS);
- a detailed description of every pathological finding, including location,
  size, and morphology, or an explicit statement that no abnormal finding was
  observed.

## Time documentation

| Event | Meaning |
| --- | --- |
| E1: Patient enters examination room | Start of staff and room occupancy |
| E2: Endoscopy begins | The device enters the body opening |
| E3: Endoscope withdrawal begins | Required for quality assurance and withdrawal-time calculation |
| E4: Endoscopy ends | The device is removed from the body opening |

Cecal and ileal intubation may be recorded as booleans. Cecal intubation is not
necessarily meaningful after surgery; a future reviewed requirement should
define examination completeness and its relationship to documented visualized
anatomy.

The morphology context includes the Paris classification, the NBI International
Colorectal Endoscopic Classification (NICE), and the Japan NBI Expert Team
(JNET) classification. Their exact clinical use and required combinations must
be governed by the tracked, reviewed requirements rather than inferred from
this historical page.

## Historical configuration sketch

The following sketch is retained as source context. It is not guaranteed to be
valid loader YAML and is not a production schema:

```yaml
coloreg_colonoscopy_requirements:
  patient_data:
    patient_id: "required, but previously marked to skip"
    patient_first_name: true
    patient_last_name: true
    patient_birth_date: true
    previous_bowel_surgery: "yes | no | unknown"
    last_known_colonoscopy_date: "date | none | unknown"
  examination_data:
    examination: true
    examination_indication:
      - screening
      - symptomatic
      - planned_resection
      - follow_up
      - surveillance
      - other
      - unknown
    sedation:
      - propofol
      - midazolam
      - none
      - other
      - unknown
    times:
      - ExaminationStart
      - WithdrawalStart
      - ExaminationEnd
    bowel_preparation: BBPS
    deepest_intubation: colon_location
  findings_data:
    focus: polyp
    location: colonoscopy_default
    rectum_or_sigmoid_requires: location_cm
    preferred_size: size_mm
    fallback_size: size_categorical
    morphology:
      under_10_mm: [paris_classification]
      at_least_10_mm: [paris_classification, nice_classification]
      at_least_20_mm: [LST_classification, nice_classification]
    intervention: [biopsy_or_resection, clip]
```
