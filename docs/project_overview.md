# Project overview

EndoReg-DB is a reusable Django application for clinical and research data. It
provides data models and terminology, report and video processing, and APIs for
ingesting and reading clinical media. It supports local development and
deployment as part of a larger system.

## Main capabilities

- **Clinical data:** patients, examinations, findings, classifications,
  interventions, products, and related records.
- **Reports and media:** report import and processing, video import,
  anonymization, frame extraction, and streaming.
- **Terminology and seed data:** reusable definitions and YAML-backed data
  loaded through the Django setup commands.
- **Integration:** authenticated APIs, center-scoped access, and configurable
  deployment roles.
- **Development and operations:** reproducible `devenv` environment, tests,
  profiling tools, and operational runbooks.

## Repository structure

| Path | Purpose |
| --- | --- |
| `endoreg_db/` | Django application, services, models, management commands, and seed data |
| `docs/` | Architecture contracts, API references, guides, and runbooks |
| `setup/` | Configuration, privacy, and AI setup guides |
| `feature-tracking/` | Feature definitions, acceptance criteria, and readiness evidence |
| `tests/` | Automated tests grouped by application area |
| `workflow/` | Reusable workflow profiles |

## Deployment roles and API boundary

The package supports standalone, site-node, local-study-server, and central-hub
roles. The central hub role requires authenticated API uploads to declare a
`center_key`; API requests cannot fall back to a default center. Optional
node-to-node transfer endpoints are gated to central-hub deployments.

Applications and AI consumers should use the center-scoped media APIs for
reports, videos, frames, and patient timelines rather than reading storage
directories directly. Deployment details and transport requirements are in the
[configuration guide](../setup/CONFIGURATION_GUIDE.md), [hub ingest operations
guide](hub_ingest_operations.md), and [hub contract deployment
note](deployment_note_hub_contract.md).

## Storage and security

Production roles validate database, authentication, transport, and protected
storage settings at startup. Media storage follows the configured storage
profile and deployment-provided encrypted storage boundary. Security-relevant
workflows emit structured logs; audit coverage depends on the workflow.

Read the [data privacy guide](../setup/HOW_WE_KEEP_DATA_PRIVATE.md),
[key rotation guide](key_rotation.md), and [video storage normalization
contract](video_storage_normalization.md) before making related changes.
