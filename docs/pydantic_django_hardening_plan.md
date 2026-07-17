# Pydantic and Django Hardening Plan

> Status tracking was migrated to `feature-tracking/TypeSafety.yml`. This
> document is retained as technical analysis and must not carry an independent
> completion status.

## Purpose

This note documents the current risk profile around the repository's Pydantic usage, especially where Pydantic models intersect with Django ORM objects, YAML-backed models, and multi-inheritance model composition.

The immediate goals are:

- make hidden query behavior explicit
- reduce future multiple-inheritance breakage
- keep API contracts strict where needed without making internal flows brittle
- establish practical guardrails for future model changes

## Executive Summary

The current repository uses Pydantic in two distinct modes:

1. strict contract mode
2. domain aggregation and serialization mode

Strict contract mode is used for request and response payloads such as:

- `lx-data-models/lx_dtypes/models/contracts/case_resolution.py`
- `lx-data-models/lx_dtypes/models/contracts/pdf_redaction.py`
- `endoreg_db/schemas/lookup_state.py`

Domain aggregation and serialization mode is used for:

- YAML-backed `lx_dtypes` models
- `RequirementLinks`
- ledger and knowledge-base export models
- helper containers that wrap Django objects

The design is broadly coherent, but the main technical risks are:

- hidden ORM queries behind `.links` properties
- structurally fragile multiple inheritance in the `lx_dtypes` Pydantic layer
- unclear ownership when fields evolve across Django, Pydantic, and DataDict layers

## Current Findings

### 1. No active direct Pydantic MRO field collision was found

The current multiple-inheritance models do not appear to define duplicate field names across direct Pydantic parents.

The best example is:

- `lx-data-models/lx_dtypes/models/knowledge_base/classification_choice_descriptor/ClassificationChoiceDescriptor.py`

Its mixins currently contribute distinct fields:

- `DescriptorTypeMixin`
- `NumericDescriptorMixin`
- `SelectionDescriptorMixin`
- `BooleanDescriptorMixin`
- `UnitMixin`
- `TextDescriptorMixin`

This means there is no confirmed live bug today caused by duplicate field definitions in that class.

### 2. The inheritance structure is still fragile

Several mixins inherit from `BaseModel` directly instead of being plain Python mixins. Examples:

- `lx-data-models/lx_dtypes/models/knowledge_base/classification_choice_descriptor/DescriptorTypeMixin.py`
- `lx-data-models/lx_dtypes/models/knowledge_base/classification_choice_descriptor/TextDescriptorMixin.py`
- `lx-data-models/lx_dtypes/models/knowledge_base/classification_choice_descriptor/UnitMixin.py`

This is workable, but it means any future addition of:

- a duplicate field name
- a validator
- a serializer
- `model_config`

can affect the final schema through Python MRO in a way that is easy to miss in review.

There is also at least one redundant inheritance chain:

- `lx-data-models/lx_dtypes/models/base/file/pydantic/FilesAndDirs.py`

`FilesAndDirsModel` inherits `PathMixin` and `AppBaseModel`, while `PathMixin` already inherits `AppBaseModel`. This is not breaking anything now, but it increases ambiguity around inherited behavior.

### 3. Hidden ORM work is the most concrete runtime risk

The most important current issue is not `from_attributes=True`. It is the use of Pydantic wrapper models around live Django objects while `.links` properties execute ORM lookups.

The clearest example is:

- `endoreg_db/models/medical/patient/patient_examination.py`

Historically, `PatientExamination.links` performed live data gathering using:

- `self.indications.all()`
- `PatientLabValue.objects.filter(patient=self.patient)`
- `self.patient_findings.all()`
- `patient_finding.active_classifications`
- `patient_finding.active_interventions`

Those inner helpers also return querysets:

- `endoreg_db/models/medical/patient/patient_finding.py`

That property has been removed. The canonical path now uses
`load_patient_examination_for_links()` followed by
`build_patient_examination_links()` in the service layer. The loader has a bounded
query plan and aggregation performs no queries.

### 4. Prefetching exists, but coverage is partial

`load_patient_exam_for_eval()` in:

- `endoreg_db/services/lookup_service.py`

does a useful amount of `select_related()` and `prefetch_related()`, but it does not fully align with everything later touched in `.links`.

Examples of likely miss paths:

- `PatientExamination.indications`
- `PatientLabValue.objects.filter(patient=self.patient)`
- intervention and classification subtrees accessed through `active_classifications` and `active_interventions`

This creates a gap between "the object is loaded for evaluation" and "all downstream traversals are actually prefetched".

### 5. The `ListFieldSerializationMixIn` is only partially MRO-aware

`lx-data-models/lx_dtypes/models/base/app_base_model/pydantic/InterfaceMixIns.py`

contains `_get_all_list_fields()`, which suggests the intent to aggregate list-type fields across the inheritance tree.

However, the actual validation and dumping paths currently call `list_type_fields()` directly instead of `_get_all_list_fields()`.

That means if list-serialized fields are ever split across multiple base classes, the implementation may silently miss some of them.

### 6. The source of truth is distributed across three layers

For many concepts, the effective contract spans:

1. Django model
2. Pydantic model
3. DataDict or export model

This is especially visible in `lx-data-models`, where:

- `AppBaseModel` controls validation and YAML behavior
- `DDictMixIn` materializes export-oriented structures
- Django models remain the persistence layer in `endoreg_db`

This is a valid architecture, but it means field changes are easy to apply incompletely.

## Risk Classification

### High Risk

- repeated hidden ORM access in requirement evaluation paths
- performance regressions caused by `.links` properties reading live relations

### Medium Risk

- future Pydantic field collisions across mixins
- future config collisions if mixins start defining `model_config`
- silent divergence between list-type field declarations across inherited classes

### Medium to Long-Term Risk

- field changes being applied to only one of Django, Pydantic, or DataDict layers
- internal payload brittleness from using strict contract models in evolving internal flows

## Hardening Plan

The plan below is ordered by value relative to effort.

### Phase 1: Make query behavior explicit

#### 1. Introduce a documented rule for `.links`

Document and enforce this rule:

- `.links` must be treated as potentially query-producing unless explicitly proven otherwise

This should become a code review expectation for:

- `build_patient_examination_links()`
- `PatientFinding.links`
- `Requirement.links`
- other model-level link aggregators

#### 2. Add query-count regression tests for requirement evaluation

Add targeted tests around:

- `load_patient_exam_for_eval()`
- `Requirement.evaluate()`
- `build_patient_examination_links()`

Use Django query-count assertions to detect accidental N+1 behavior.

Priority targets:

- one patient examination with several findings
- several findings each with classifications and interventions
- several linked requirement sets

Desired outcome:

- query count stays bounded after prefetched load
- changes to `.links` behavior become visible in tests

#### 3. Align prefetch plans with actual traversal paths

Review evaluation loaders and update them so they use the canonical
`load_patient_examination_for_links()` object graph before calling
`build_patient_examination_links()`.

At minimum, inspect and likely cover:

- `indications`
- `indications__examination_indication`
- `indications__indication_choice`
- `patient_findings__classifications`
- `patient_findings__interventions`
- any relations reached from `active_classifications` and `active_interventions`
- patient lab values if they are part of evaluation-critical flows

The target is not "prefetch everything". The target is "prefetch the exact relations traversed in hot paths".

### Phase 2: Reduce multiple-inheritance fragility

#### 4. Add a design rule for Pydantic mixins

Adopt this repository rule:

- Pydantic mixins must not define `model_config`
- Pydantic mixins must not reuse an existing field name from another mixin or base unless the override is deliberate and documented

This should be documented near the `lx_dtypes` base model layer.

#### 5. Add an automated duplicate-field check for multi-parent Pydantic models

Add a lightweight test or script that scans Pydantic classes with multiple parents and fails if two direct parents define the same field name.

This catches the most dangerous future failure mode early.

Scope:

- `lx-data-models/lx_dtypes/models/**`

#### 6. Simplify redundant inheritance

Where a class inherits both:

- a mixin/base that already extends `AppBaseModel`
- and `AppBaseModel` again

prefer the minimal inheritance chain.

Primary candidate:

- `lx-data-models/lx_dtypes/models/base/file/pydantic/FilesAndDirs.py`

This is a maintenance cleanup rather than a correctness fix, but it reduces ambiguity.

### Phase 3: Fix serialization-layer inconsistencies

#### 7. Make `ListFieldSerializationMixIn` consistently MRO-aware

Update:

- `_coerce_list_fields()`
- `model_dump()`

to use `_get_all_list_fields()` instead of `list_type_fields()` directly, or remove `_get_all_list_fields()` if the repository deliberately wants only the leaf class to decide.

Right now the code suggests one design but implements another.

That should be resolved explicitly.

#### 8. Document when list fields are semantic lists vs serialized string fields

Some models intentionally use `Union[str, List[str]]` and rely on the mixin to normalize them.

Examples:

- `lx-data-models/lx_dtypes/models/knowledge_base/examination/Examination.py`
- `lx-data-models/lx_dtypes/models/ledger/center/Pydantic.py`

This is fine, but contributors need guidance on:

- when this pattern is allowed
- when plain `list[str]` should be preferred
- how YAML serialization expectations drive the choice

### Phase 4: Make ownership of field changes explicit

#### 9. Add a field-change checklist to contributor documentation

When adding or renaming a field, reviewers should ask:

1. Does the Django model need it?
2. Does the Pydantic contract model need it?
3. Does the DataDict or serialized export model need it?
4. Do YAML import or dump paths need coverage?
5. Do API serializers or DRF serializers need matching updates?
6. Do query prefetch paths need updating if evaluation logic will traverse it?

This checklist should become standard for cross-layer model changes.

#### 10. Separate strict boundary contracts from internal state carriers

Retain `extra="forbid"` for:

- external API request and response contracts
- durable validated file contracts where unknown keys are errors

Avoid forcing the same strictness on:

- evolving internal state payloads
- transitional cache payloads
- internal service-to-service envelopes

This split already exists in practice. It should be treated as a deliberate architectural rule.

## Recommended Documentation Rules

These should be added to model-layer documentation and used in reviews.

### Rule 1

Use `extra="forbid"` only for true boundary contracts.

### Rule 2

Assume `.links` properties are query-producing until proven otherwise.

### Rule 3

Do not add `model_config` to a Pydantic mixin without a compelling reason and explicit review.

### Rule 4

If a model field exists in Django, Pydantic, and DataDict layers, changes must be reviewed across all three in the same change set.

### Rule 5

If evaluation code consumes a Django object graph repeatedly, provide one loader function with the required prefetch contract and test the query count.

## Suggested Implementation Order

Recommended order for actual hardening work:

1. add query-count tests around requirement evaluation
2. align evaluation loaders with the service-owned link traversal
3. add duplicate-field detection for multi-parent Pydantic models
4. clean up `ListFieldSerializationMixIn`
5. simplify redundant inheritance
6. add contributor checklist for cross-layer field changes

## What Not To Do

- do not replace the current Pydantic layer wholesale
- do not remove strict contracts from API boundaries
- do not assume `from_attributes=True` is the main problem
- do not optimize prefetching without checking which relations are actually traversed

The biggest immediate gains come from making evaluation paths measurably query-safe and from putting guardrails around future Pydantic mixin growth.
