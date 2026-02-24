# Docs Folder Status (Transition Cleanup)

## Purpose
This `docs/` folder currently contains a mix of:
- temporary implementation handoff notes
- design drafts
- longer-lived technical guides

During the new reporting/setup implementation, **root-level docs are treated as working notes** and may be deleted after rollout.

## Canonical Documentation Direction
For the new setup, create and maintain **dedicated wiki pages** (drafted in `docs/wiki/` first, then moved to the project wiki when implementation stabilizes).

New durable docs added for this purpose:
- `docs/wiki/new_setup_overview.md`
- `docs/wiki/new_setup_general_purpose.md`
- `docs/wiki/dataloader_yaml_authoring.md`
- `docs/wiki/README.md`

## Temporary / Implementation-Phase Docs (candidate cleanup after rollout)
Examples in this repository root:
- `docs/frontend_reporting_pages_design.md`
- `docs/frontend_agent_lookup_contract.md`
- `docs/frontend_agent_url_contract.md`
- `docs/handoff_report_pdf_renderer.md`
- `docs/handoff_pypi_release.md`

Keep these while implementing. Remove or merge into wiki docs after rollout.

## Writing Rules (project-specific)
- Prefer YAML config over hardcoded setup instructions where applicable.
- Reference `load_base_db_data` when documenting base dataset bootstrap flows.
- API examples should use `snake_case` (no camelCase in contracts/docs).
- Video/report heavy endpoints belong under `/api/media/...` in examples.

