# EndoReg-DB Docs

This directory contains the maintained in-repo documentation for setup,
operations, API behavior, media workflows, and hardening plans. Generated docs
output should stay out of version control.

## Start Here

- `docs/wiki/new_setup_overview.md`: setup overview and system map
- `docs/wiki/new_setup_general_purpose.md`: operating model and goals
- `docs/wiki/README.md`: wiki page index

## Data Loading

- `docs/wiki/dataloader_yaml_authoring.md`: authoring YAML input for the dataloader
- `docs/wiki/dataloader_layers_and_naming.md`: dataloader module layering and naming
- `docs/supported_formats_csv.md`: supported tabular import formats

## Media And Annotation

- `docs/annotation_export_guide.md`: exporting annotations and training images
- `docs/annotation_source_scope_training.md`: annotation source scope for multilabel training
- `docs/video_frame_extraction_contract.md`: frame extraction behavior and guarantees
- `docs/video_temporal_inference.md`: temporal prediction options and smoothing behavior
- `docs/wiki/frame_annotation_current_support.md`: frame annotation API and frontend status

## API And Deployment

- `docs/api_route_test_matrix.md`: route coverage and matching tests
- `docs/deployment_note_hub_contract.md`: hub contract changes for downstream consumers
- `docs/local_study_server_deployment.md`: local study server deployment notes
- `docs/wiki/hub_ingest_current_state.md`: current hub ingest behavior
- `docs/wiki/hub_ingest_gap_closure.md`: target hub ingest closure plan

## Architecture And Hardening

- `docs/ai_model_configuration.md`: AI model configuration
- `docs/configurable_ai_setup.md`: configurable AI setup
- `docs/dtypes_lookup_module_entrypoint.md`: dtypes lookup entrypoint
- `docs/production_workflow_hardening_plan.md`: production workflow hardening
- `docs/pydantic_django_hardening_plan.md`: Pydantic and Django hardening
- `docs/video_typing_integration_plan.md`: video typing integration
- `docs/repo_alignment_todo.md`: repository alignment TODOs
- `docs/colonoscopy-requirements-plan.md`: colonoscopy requirements draft

## Project Rules

- Prefer YAML config over hardcoded setup instructions where applicable.
- Reference `load_base_db_data` when documenting base dataset bootstrap flows.
- API examples must use `snake_case`; do not use camelCase.
- Video/report heavy endpoints belong under `/api/media/...` in examples.
