# Legacy requirement validation system

The legacy `Requirement`, `RequirementSet`, `ExaminationRequirementSet`, `RequirementOperator`, `RequirementSetType`, and `RequirementType` models were removed by migration `0013_remove_legacy_requirement_models`. They are not part of the current production model.

Current linked-model traversal uses `endoreg_db.utils.links.ModelLinks`. Do not rely on the former requirement-model API or its removed tests for active rule evaluation.
