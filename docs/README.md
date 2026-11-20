# EndoReg-DB Documentation

Start with your task below. Guides describe operation; the
[feature tracker](../feature-tracking/README.md) records readiness and evidence.

## Upgrading or recovering encrypted media

| Your task | Start here |
| --- | --- |
| Use the application during maintenance | Ask the operator when imports and editing can resume. Report missing or unreadable media; do not delete or reimport it as a repair. |
| Understand what changed | [Current changelog](../changelog.md): effects on existing and partially migrated machines. |
| Upgrade v1 files or resume a partial migration | [Operator procedure](secret_rotation.md#v1-machines-and-interrupted-media-migrations): prerequisites, preview, apply and verification. |
| Interpret a migration result | [Result guide](secret_rotation.md#read-the-media-migration-result): counts and next actions. |
| Review or implement the format | [Format contract](secret_rotation.md#format-versions) and [review evidence](../feature-tracking/reports/lxenc_v2_review.md). |

## Deployment and Operations

| Task | Runbook |
| --- | --- |
| Configure or deploy a node | [Configuration](../setup/CONFIGURATION_GUIDE.md), [study server](local_study_server_deployment.md) |
| Diagnose imports or transfers | [Hub operations](hub_ingest_operations.md#diagnostics-and-restart), [incident workflow](hub_ingest_operations.md#incident-workflow) |
| Fix login or center access | [Center access](center_access_operations.md#diagnosis) |
| Check audit integrity | [Audit ledger](audit_ledger_operations.md#operation-and-alerting) |
| Recover a database and media | [Backup requirements](backup_recovery.md), [hub restore](hub_ingest_operations.md#backup-and-restore) |
| Inspect storage or interrupted video work | [Storage inventory](video_storage_normalization.md#storage-inventory), [recovery](video_storage_normalization.md#abort-and-recovery) |
| Reconcile video formats | [Reconciliation service](ops/nixos/video_format_reconciliation.md) |
| Inspect hub behavior | [Hub ingest reference](wiki/hub_ingest_current_state.md) |

## Contributor roadmap

1. Read the [project overview](project_overview.md) and [privacy guide](../setup/HOW_WE_KEEP_DATA_PRIVATE.md).
2. Follow the [quick start](../README.md#quick-start); run `devenv tasks run test:fast`.
3. Try the [Django shell examples](developer_examples.md) and choose a workflow below.
4. Read its feature criteria, implementation and tests before editing.
5. Follow [AGENTS.md](../AGENTS.md) for coordination and verification.

## Project development

| Area | Focus | Tracked work |
| --- | --- | --- |
| Reliable imports | Input validation, duplicate detection, retries, and recovery | [Import pipeline](../feature-tracking/ImportPipelineRobustness.yml) |
| Media lifecycle | Bounded video storage, streaming, cleanup, and timeline integrity | [Video storage](../feature-tracking/VideoStorageNormalization.yml) |
| Clinical reporting | Report import, editing, persistence, and assisted reporting | [Reporting](../feature-tracking/Reporting.yml), [API integration](../feature-tracking/AssistedReportingApiIntegration.yml) |
| Shared clinical definitions | Typed, repeatable loading of terminology and YAML data | [Data loading](../feature-tracking/DataLoading.yml) |
| APIs and privacy | Stable endpoints, access control, anonymization, and review | [API contracts](../feature-tracking/ApiContracts.yml), [anonymization](../feature-tracking/Anonymization.yml) |
| Maintainability | Clear ownership, focused helpers, useful tests, and documentation | [Code quality](../feature-tracking/CodeQuality.yml), [documentation](../feature-tracking/Documentation.yml) |

## Browse by task

- [Install, configure, or deploy](#deployment-and-operations)
- [Understand data models](#architecture-and-domain-contracts)
- [Use APIs or manage access](#api-access-and-security)
- [Load data or configure reports and AI](#data-loading-and-reporting)
- [Work with media and annotations](#media-and-annotation)
- [Explore design history](#historical-and-migrated-planning-documents)

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

- [Data privacy and storage overview](../setup/HOW_WE_KEEP_DATA_PRIVATE.md)
- [Key rotation and recovery](key_rotation.md)
- [Online secret rotation contracts](secret_rotation.md)
- [API route test matrix](api_route_test_matrix.md)
- [Center access operations](center_access_operations.md)
- [Anonymization and release contract](anonymization_contract.md)
- [Assisted-reporting API integration](assisted_reporting_api_integration.md)
- [Hub contract deployment note](deployment_note_hub_contract.md)
- [Hub transfer typed contract](hub_transfer_typed_contract.md)
- [Audit ledger operations](audit_ledger_operations.md)

## Data Loading and Reporting

- [Configuration guide](../setup/CONFIGURATION_GUIDE.md)
- [AI model setup](../setup/AI_MODEL_SETUP.md)
- [Dataloader YAML authoring](wiki/dataloader_yaml_authoring.md)
- [Legacy reference catalogue reconciliation](wiki/reference_catalog_migration.md)
- [Dataloader layers and naming](wiki/dataloader_layers_and_naming.md)
- [Supported tabular import formats](supported_formats_csv.md)
- [Stable concurrent report imports](report_import_concurrency_implementation.md)
- [AI model configuration](ai_model_configuration.md)
- [Configurable AI setup](configurable_ai_setup.md)

## Media and Annotation

Start with [application storage and cleanup](storage_lifecycle.md) for the
current repository-wide storage model. Read [video storage
normalization](video_storage_normalization.md) before changing video import,
storage, transcoding, publication, or cleanup behavior.

- [Video import concurrency](video_import_concurrency_contract.md)
- [Video timestamp and frame-rate call-site inventory](video_pts_fps_callsite_inventory.md)
- [HLS permissions and streaming](video_hls_permissions_and_streaming.md)
- [Video frame extraction](video_frame_extraction_contract.md)
- [Video temporal inference](video_temporal_inference.md)
- [Video format reconciliation services](ops/nixos/video_format_reconciliation.md)
- [Frame annotation support](wiki/frame_annotation_current_support.md)
- [Annotation export](annotation_export_guide.md)
- [Annotation source scope for training](annotation_source_scope_training.md)
- [Reviewed training annotation selection](annotation_training_selection.md)
- [Processed training frame streams](training-streams.md)
- [Video reimport processing budget](video_reimport_budget.md)

## Reference and Supporting Material

- [API and endpoint test matrix](api_route_test_matrix.md)
- [Profiling imports and reimports](profiling.md)
- [Supported tabular import formats](supported_formats_csv.md)
- [Dataloader setup overview](wiki/new_setup_overview.md)
- [General data-loading operating model](wiki/new_setup_general_purpose.md)
- [Sphinx API reference entry point](source/index.rst)
- [Historical GitHub Wiki references](legacy_wiki_index.md)

## Generated References

- [LXDM contract-to-consumer inventory](lxdm_contract_inventory.md) is generated
  by `scripts/audit_lxdm_contract_usage.py`; regenerate it instead of editing it
  manually.
- `source/*.rst` contains the Sphinx API-reference entry points. Build output is
  disposable and is not a documentation source.

## Historical and Migrated Planning Documents

These pages explain earlier designs and decisions. Their linked feature YAML
records current scope and evidence.

- [Colonoscopy requirements plan](colonoscopy-requirements-plan.md) →
  [`Colonoscopy.yml`](../feature-tracking/Colonoscopy.yml)
- [Production workflow hardening plan](production_workflow_hardening_plan.md) →
  [`ProductionWorkflow.yml`](../feature-tracking/ProductionWorkflow.yml)
- [Pydantic and Django hardening plan](pydantic_django_hardening_plan.md) →
  [`LxDtypesModelStandardization.yml`](../feature-tracking/LxDtypesModelStandardization.yml)
- [Repository alignment TODO](repo_alignment_todo.md) →
  [`StorageSecurity.yml`](../feature-tracking/StorageSecurity.yml)
- [Video typing integration plan](video_typing_integration_plan.md) →
  [`TypeSafety.yml`](../feature-tracking/TypeSafety.yml)
- [Hub ingest gap-closure plan](wiki/hub_ingest_gap_closure.md) →
  [`HubIngest.yml`](../feature-tracking/done/HubIngest.yml)

- [Report import design](report_import_concurrency_implementation.md) →
  [Reporting.yml](../feature-tracking/Reporting.yml)
- [Historical video data-retention investigation](video_datenerhalt_gc02_gc10_20260923.md) →
  [VideoStorageNormalization.yml](../feature-tracking/VideoStorageNormalization.yml)

## Authoring Rules

Write short English Markdown guides for human readers. Lead with the task,
required conditions, steps, expected result, and recovery action. Link to the
owning contract instead of repeating it. Preserve exact commands and identifiers.
Keep readiness assessments in feature YAML and follow
[documentation governance](documentation_governance.md) for review and publication.
