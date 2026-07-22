# Assisted reporting API integration

The assisted-reporting frontend uses two canonical API mounts:

- `/endoreg-api/` for patient, examination, media, and report persistence;
- `/dtypes-api/` for knowledge-base templates, terminology bundles, finding
  catalogs, classifications, and typed patient-finding mutations.

`/api/` and `/base_api/` are compatibility aliases. New frontend calls must use
the helpers in `frontend/src/api/axiosInstance.ts` and must not introduce direct
compatibility-path dependencies.

## Finding routes

The reporting UI loads a finding catalog for the selected examination through
`GET /dtypes-api/examinations/{examination_id}/findings/`. There is no global
`GET /endoreg-api/findings/` contract. Patient findings use
`/dtypes-api/patient-findings/` and the `patient_examination` query parameter.
The frontend sends snake_case request payloads; its central Axios response
boundary converts response keys to camelCase.

`LX_DTYPES_HOST_MODELS_MODULE` must resolve to
`endoreg_db.integrations.lx_dtypes_host_models`. The adapter authenticates
requests and scopes patient findings to the authenticated user's center. A
missing or unimportable adapter is a startup-readiness failure.

## Terminology bundles

The bundle API reads a JSON registry from `LX_DTYPES_TERMINOLOGY_REGISTRY`, or
from `LX_DTYPES_KB_REGISTRY` for compatibility. The registry has this minimum
shape:

```json
{"modules": {}}
```

NixOS deployments provision the registry at
`<encrypted-data-root>/terminology/registry.json` and imported packages below
`<encrypted-data-root>/terminology/packages/`. Both locations remain inside the
managed encrypted storage boundary. A bundle exported by
`lx-terminology-editor` can be uploaded as a ZIP through
`POST /dtypes-api/terminology/bundles/import`. Published module names and
versions are immutable: importing an existing identity returns HTTP 409 rather
than replacing the installed package. Registry publication uses an atomic file
replacement.

Listing bundles is authenticated by the host deployment. Importing or selecting
a bundle additionally requires staff/superuser status, the `terminology:write`
role, or a role satisfying the host's `data:write` policy.

## Deployment checks

Before serving traffic, runtime checks verify that:

1. the host adapter setting exists and is importable;
2. the registry environment setting exists;
3. the registry file is readable JSON with a `modules` object;
4. the normal database, encrypted-storage, and Nginx checks pass.

The deployed artifact set must record the lx-annotate, endoreg_db, and
lx_dtypes versions together. The frontend and Python wheel are one compatibility
unit; deploying only one side can reintroduce route drift.

## Failure diagnosis

| Symptom | Meaning | Check |
| --- | --- | --- |
| Bundle list returns 404 | Registry path is missing in an older deployment, or the configured file does not exist | Inspect the declarative service environment and runtime-readiness output |
| Finding route returns 500 mentioning `LX_DTYPES_HOST_MODELS_MODULE` | Production settings did not export the host adapter | Verify the packaged `settings_prod.py` and service environment |
| Patient-finding route returns 401/403 | Authentication or write role is missing | Inspect the authenticated principal and synced groups |
| Patient-finding route returns an empty list | No active finding is visible in the authorized examination/center scope | Verify the examination ID and center ownership |
| `/endoreg-api/findings/` returns 404 | Unsupported global route | Update the caller to use the examination-scoped dtypes route |

Do not expose registry filesystem paths, tokens, patient identifiers, or service
secrets in client-facing error messages.
