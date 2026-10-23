# Project overview

EndoReg-DB helps clinical teams organize endoscopy data and prepare it for
research. It provides reusable Django models, terminology, media processing,
and authenticated APIs. Host applications use these components to build clinical
and research workflows.

For a guided introduction, follow the [contributor roadmap](README.md#contributor-roadmap).
For development areas and their acceptance criteria, see
[project development](README.md#project-development).

## What the system does

- **Clinical records:** connect patients, examinations, findings,
  classifications, interventions, and products.
- **Reports:** import documents, process their text, and support reporting workflows.
- **Video:** import and anonymize recordings, extract frames, and stream media.
- **Terminology:** load reusable definitions and seed data from YAML.
- **Integration:** expose authenticated APIs with access scoped to clinical centers.

## Follow the data

1. A local watcher or authenticated API receives a report or video.
2. The import workflow checks the input and resolves its clinical center.
3. Shared services process the content and link it to clinical records.
4. Authorized users review results, annotate media, or prepare exports.
5. Retention policies govern storage and cleanup.

The [storage lifecycle guide](storage_lifecycle.md) explains the media lifecycle.
The [report import guide](report_import_concurrency_implementation.md) describes
report processing and concurrency.

## How the repositories fit together

EndoReg-DB supplies backend models, services, and APIs. LX-Annotate provides the
application interface. Shared contracts come from `lx_dtypes` in lx-data-models;
lx-anonymizer supplies anonymization components.

Within this repository, `endoreg_db/` contains the application, `tests/` its tests,
and `setup/` its setup guides. Use [structure.yml](../structure.yml) to navigate
module ownership and [the model map](model_layer_map_for_agents.md) to understand
model and service boundaries.

## Deployment roles

The package supports standalone, site-node, local-study-server, and central-hub
roles. The central hub requires authenticated uploads with an explicit
`center_key`. Node-to-node transfer endpoints are gated to central-hub deployments.

Applications use center-scoped APIs for reports, videos, frames, and patient
timelines. See the [configuration guide](../setup/CONFIGURATION_GUIDE.md),
[hub operations guide](hub_ingest_operations.md), and
[hub deployment contract](deployment_note_hub_contract.md) for deployment details.

## Privacy and storage

Production roles check database, authentication, transport, and storage settings
at startup. Deployments provide the encrypted storage boundary required by their
storage profile. Structured logging and audit coverage depend on the workflow.

Before changing these areas, read the
[privacy guide](../setup/HOW_WE_KEEP_DATA_PRIVATE.md),
[key rotation guide](key_rotation.md), and
[video storage contract](video_storage_normalization.md).
