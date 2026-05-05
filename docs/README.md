# EndoReg-DB Documentation Index

## Purpose
This `docs/` folder is the in-repo documentation source for current behavior and
operational guidance.

## Core Setup and Workflow Docs
Primary pages for setup/dataloader/hub/frame-annotation behavior:
- `docs/wiki/new_setup_overview.md`
- `docs/wiki/new_setup_general_purpose.md`
- `docs/wiki/dataloader_yaml_authoring.md`
- `docs/wiki/dataloader_layers_and_naming.md`
- `docs/wiki/frame_annotation_current_support.md`
- `docs/annotation_export_guide.md`
- `docs/video_frame_extraction_contract.md`
- `docs/wiki/hub_ingest_current_state.md`
- `docs/wiki/hub_ingest_gap_closure.md`
- `docs/wiki/README.md`

## Additional Technical Notes
Other documents in `docs/` cover focused implementation topics. Examples:
- `docs/frontend_reporting_pages_design.md`
- `docs/frontend_agent_lookup_contract.md`
- `docs/frontend_agent_url_contract.md`
- `docs/handoff_report_pdf_renderer.md`
- `docs/handoff_pypi_release.md`
- `docs/pydantic_django_hardening_plan.md`

## Writing Rules (project-specific)
- Prefer YAML config over hardcoded setup instructions where applicable.
- Reference `load_base_db_data` when documenting base dataset bootstrap flows.
- API examples should use `snake_case` (no camelCase in contracts/docs).
- Video/report heavy endpoints belong under `/api/media/...` in examples.
