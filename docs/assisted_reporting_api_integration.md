# Assisted reporting API integration

The assisted-reporting frontend uses two API mounts:

- `/endoreg-api/` for patient, examination, media, and report persistence;
- `/dtypes-api/` for knowledge-base templates, terminology bundles, finding
  catalogs, classifications, and typed patient-finding mutations.

`/dtypes-api/` is the only knowledge-base API mount exposed by lx-annotate.
`/base_api/` is not a supported application contract. Frontend calls must use
the helpers in `frontend/src/api/axiosInstance.ts`.

## Governed terminology lifecycle

Terminology and report-template data reaches the clinical frontend through one
artifact path:

1. Authors change YAML in lx-data-models or an approved terminology editor.
2. lx-data-models validation loads the complete module graph and runs schema,
   semantic, report-template readiness, and focused clinical tests.
3. The approved export receives an immutable module name and version. Reusing
   an existing identity is rejected.
4. An authorized terminology administrator imports the export ZIP. The server
   extracts it below the encrypted terminology package root, loads it through
   `KnowledgeBaseResolver`, and only then publishes a `filesystem` source in
   the registry. Bundles shipped by the `lx-dtypes` wheel are registered as
   `provider` sources with the digest from the wheel's package catalog.
5. Activation atomically writes the selected `{module_name, version}` identity
   into the same registry. Active state is not browser state, worker memory, or
   a mutable process-environment override.
6. Startup readiness parses the registry, verifies that its active identity is
   registered, and loads that exact version before accepting traffic.
7. `/dtypes-api/` resolves templates and finding filters from that active
   version. The frontend receives the governed identity and clinical content,
   never registry or package filesystem paths.

Local checkouts, the current working directory, `LOOKUP_DTYPES_DATA_ROOT`, Nix
source-tree inputs, and browser-selected default modules are not deployment
sources. A wheel resource is addressed through its provider descriptor; its
resolved `site-packages` or Nix-store path is never persisted. Clinical runtime
selection always requires a registered, versioned identity.

## Finding routes

The reporting UI loads a finding catalog for the selected examination through
`GET /dtypes-api/examinations/{examination_id}/findings/`. Every catalog request
sends the selected `module_name` and `module_version`; it sends
`patient_examination_id` when a patient examination supplies the reporting
context. The server must resolve that exact module/version and must not silently
substitute the registry's active identity.

The graph and examination-specific reporting context use the same identity:

```text
GET /dtypes-api/knowledge-bases/{module_name}/{module_version}/graph
GET /dtypes-api/knowledge-bases/{module_name}/{module_version}/examinations/{examination_name}/reporting-context
```

There is no global `GET /endoreg-api/findings/` contract. Patient findings use
`/dtypes-api/patient-findings/` and the `patient_examination` query parameter.
The frontend sends snake_case request payloads; its central Axios response
boundary converts response keys to camelCase.

`LX_DTYPES_HOST_MODELS_MODULE` must resolve to
`endoreg_db.integrations.lx_dtypes_host_models`. The adapter authenticates
requests and scopes patient findings to the authenticated user's center. A
missing or unimportable adapter is a startup-readiness failure.

## Terminology bundles

The bundle API and knowledge-base resolver read the same JSON registry from the
single canonical setting `LX_DTYPES_KB_REGISTRY`. A traffic-serving registry
has this minimum shape:

```json
{
  "active": {
    "module_name": "gastroenterology_reporting",
    "version": "2026.07.31"
  },
  "modules": {
    "gastroenterology_reporting": {
      "2026.07.31": {
        "sources": [{
          "kind": "provider",
          "provider": "lx_dtypes.builtin",
          "content_sha256": "<64-character catalog digest>"
        }]
      }
    }
  }
}
```

The provider resolves the matching package resource from the installed wheel at
load time and verifies its catalog digest. Imported packages instead use an
explicit deployment-owned source:

```json
{
  "sources": [{
    "kind": "filesystem",
    "input_dirs": ["/managed/encrypted/terminology/packages/gastroenterology_reporting/2026.07.31"]
  }]
}
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

Registry paths and `input_dirs` are server-private and are not returned by the
bundle API. Listing bundles is authenticated by the host deployment. Importing or selecting
a bundle additionally requires staff/superuser status, the `terminology:write`
role, or a role satisfying the host's `data:write` policy.

## Deployment checks

Before serving traffic, runtime checks verify that:

1. the host adapter setting exists and is importable;
2. the registry environment setting exists;
3. bootstrap has registered and fully loaded every package-catalog bundle;
4. a missing, empty, or no-active registry has received the configured/default
   packaged identity;
5. a stale active built-in provider or wheel-path entry has been atomically
   migrated to the matching current catalog identity, without replacing a
   custom active entry;
6. the registry contains an explicit active identity present in `modules`;
7. the exact active module and version can be loaded successfully;
8. the normal database, encrypted-storage, and Nginx checks pass.

Governed wheel deployments fail closed: bootstrap, catalog validation, digest
verification, or exact active-identity failures block readiness and traffic.
A best-effort invocation is diagnostic convenience only and is not production
readiness evidence.

The deployed artifact set must record the lx-annotate, endoreg_db, and
lx_dtypes versions together. The frontend and Python wheel are one compatibility
unit; deploying only one side can reintroduce route drift.

## Failure diagnosis

| Symptom | Meaning | Check |
| --- | --- | --- |
| Service refuses to start with a registry readiness error | Registry is missing, malformed, has no active identity, or the active artifact cannot be loaded | Validate the registry and imported bundle before retrying startup |
| Finding route returns 500 mentioning `LX_DTYPES_HOST_MODELS_MODULE` | Production settings did not export the host adapter | Verify the packaged `settings_prod.py` and service environment |
| Patient-finding route returns 401/403 | Authentication or write role is missing | Inspect the authenticated principal and synced groups |
| Patient-finding route returns an empty list | No active finding is visible in the authorized examination/center scope | Verify the examination ID and center ownership |
| `/endoreg-api/findings/` returns 404 | Unsupported global route | Update the caller to use the examination-scoped dtypes route |
| `/base_api/` returns the SPA or 404 | Removed compatibility mount | Use the canonical `/dtypes-api/` contract |

Do not expose registry filesystem paths, tokens, patient identifiers, or service
secrets in client-facing error messages.
