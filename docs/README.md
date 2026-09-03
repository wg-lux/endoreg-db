# EndoReg-DB Documentation

This directory contains architecture contracts, developer references, and
operational runbooks for `endoreg-db`. Feature scope, implementation status, and
production-readiness evidence live only in [`feature-tracking/`](../feature-tracking/README.md).
Do not infer completion from a document title or checklist.

Generated Sphinx output belongs under `docs/build/` and must remain untracked.
Cross-repository ownership, lifecycle, and publication rules are defined in the
[documentation governance contract](documentation_governance.md).

## Start Here

- [Repository setup and package overview](../README.md)
- [New setup overview](wiki/new_setup_overview.md)
- [General operating model](wiki/new_setup_general_purpose.md)
- [Feature readiness tracker](../feature-tracking/README.md)
- [Model layer map for agents and maintainers](model_layer_map_for_agents.md)

## Architecture and Domain Contracts

- [Case graph persistence](case_graph_persistence.md)
- [Medical ledger integration](medical_ledger_contract.md)
- [LXDM host integration and persistence](dtypes_lookup_module_entrypoint.md)
- [LXDM contract usage audit](lxdm_contract_usage_audit.md)
- [DICOM interoperability](dicom_interoperability.md)
- [FHIR R4 export](fhir_r4_export.md)
- [Controlled k-anonymity release view](guides/k_pseudonymity_release.md)
- [Code quality and maintainability boundaries](code_quality.md)

## API, Access, and Security

- [API route test matrix](api_route_test_matrix.md)
- [Center access operations](center_access_operations.md)
- [Anonymization and release contract](anonymization_contract.md)
- [Assisted-reporting API integration](assisted_reporting_api_integration.md)
- [Hub contract deployment note](deployment_note_hub_contract.md)

## Data Loading and Reporting

- [Dataloader YAML authoring](wiki/dataloader_yaml_authoring.md)
- [Dataloader layers and naming](wiki/dataloader_layers_and_naming.md)
- [Supported tabular import formats](supported_formats_csv.md)
- [Stable concurrent report imports](report_import_concurrency_implementation.md)
- [AI model configuration](ai_model_configuration.md)
- [Configurable AI setup](configurable_ai_setup.md)

## Media and Annotation

Read [video storage normalization](video_storage_normalization.md) before changing
video import, storage, transcoding, publication, or cleanup behavior.

- [Video import concurrency](video_import_concurrency_contract.md)
- [Video timestamp and frame-rate call-site inventory](video_pts_fps_callsite_inventory.md)
- [HLS permissions and streaming](video_hls_permissions_and_streaming.md)
- [Video frame extraction](video_frame_extraction_contract.md)
- [Video temporal inference](video_temporal_inference.md)
- [Video format reconciliation services](ops/nixos/video_format_reconciliation.md)
- [Frame annotation support](wiki/frame_annotation_current_support.md)
- [Annotation export](annotation_export_guide.md)
- [Annotation source scope for training](annotation_source_scope_training.md)

## Deployment and Operations

- [Local study server deployment](local_study_server_deployment.md)
- [Hub ingest operations](hub_ingest_operations.md)
- [Current hub ingest behavior](wiki/hub_ingest_current_state.md)

## Generated References

- [LXDM contract-to-consumer inventory](lxdm_contract_inventory.md) is generated
  by `scripts/audit_lxdm_contract_usage.py`; regenerate it instead of editing it
  manually.
- `source/*.rst` contains the Sphinx API-reference entry points. Build output is
  disposable and is not a documentation source.

## Historical and Migrated Planning Documents

The following documents retain design history or durable rationale, but their
checkboxes, verdicts, and progress statements are not authoritative. Use the
linked feature definition for current status and evidence.

- [Colonoscopy requirements plan](colonoscopy-requirements-plan.md) →
  [`Colonoscopy.yml`](../feature-tracking/Colonoscopy.yml)
- [Production workflow hardening plan](production_workflow_hardening_plan.md) →
  [`ProductionWorkflow.yml`](../feature-tracking/ProductionWorkflow.yml)
- [Pydantic and Django hardening plan](pydantic_django_hardening_plan.md) →
  [`LxDtypesModelStandardization.yml`](../feature-tracking/LxDtypesModelStandardization.yml)
- [Repository alignment TODO](repo_alignment_todo.md) →
  [`StorageSecurity.yml`](../feature-tracking/StorageSecurity.yml)
- [Video typing integration plan](video_typing_integration_plan.md) →
  [`TypeSafety.yml`](../feature-tracking/done/TypeSafety.yml)
- [Hub ingest gap-closure plan](wiki/hub_ingest_gap_closure.md) →
  [`HubIngest.yml`](../feature-tracking/done/HubIngest.yml)

## Authoring Rules

- Write maintained documentation in English. Preserve exact identifiers,
  commands, protocol values, and clinical terms where translation would change
  their meaning.
- Write current behavior as a contract or runbook, not as a completion claim.
- Use **implemented** for behavior evidenced in this repository, **verified**
  only when the feature tracker records reproducible evidence and an assessor,
  and **deployed** only for an explicitly identified runtime environment.
- Put new scope, acceptance criteria, and readiness evidence in the relevant
  feature YAML before implementation.
- Use repository-relative Markdown links; do not publish workstation paths.
- Prefer YAML configuration over hardcoded setup instructions where applicable.
- Use `load_base_db_data` for base dataset bootstrap instructions.
- API examples use `snake_case`; frontend conversion happens at its central API boundary.
- Video and report heavy endpoints belong under `/api/media/...` in examples.
- Assign one canonical owner per topic and merge or clearly scope duplicates.
