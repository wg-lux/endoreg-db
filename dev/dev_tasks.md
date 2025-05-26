# Tasks
## Add Requirements
- Add Requirements which check whether a certain event has taken place
    - requirement_types should include Patient
    - operators should include "models_match_any"
    - Create Requirements for the following events
        - coro_des_implantation
        - coro_bms_implantation
        - pulmonary_embolism
        - deep_vein_thrombosis
        - stroke
        - transient_ischemic_attack
        - hip_replacement_surgery
        - knee_replacement_surgery
        - recurrent_thrombembolism
    - Create an example requirement which also checks if a model matches AND is within a certain timeframe
        - we might have to introduce a new requirement_operator for that
        - looking at a similar method from the lab_value operators might be helpful (e.g., "get_patient_lab_values_in_timeframe" from endoreg_db/utils/requirement_operator_logic/lab_value_operators.py)
- Add Tests for the new Requirements


## Implement Medication Requirements