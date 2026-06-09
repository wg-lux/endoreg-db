# Deployment Note: Hub Contract Changes for Downstream Consumers

This note summarizes the operational contract changes that downstream host
projects and deployment environments must account for when upgrading to the
current `endoreg_db` hub-aware ingest model.

## Who should read this

This note applies to:

- host Django projects embedding `endoreg_db`
- operators deploying watcher or API ingest in production
- teams upgrading from local name-based center routing
- downstream applications that previously patched package behavior at the view
  layer

## Summary of changes

The package now treats hub ingest and center scoping as core behavior rather
than downstream override behavior.

The important contract shifts are:

- `center_key` is the canonical machine-facing center identifier
- `watcher` and `api` are both first-class ingest boundaries
- both ingest boundaries create `UploadJob` records and converge on the same
  shared ingest services
- hub-mode API policy is controlled by package settings rather than downstream
  view overrides
- center-scoped media reads are enforced in the package layer
- transfer ingest exists as an optional secondary boundary and is disabled by
  default
- content-hash deduplication now participates in ingest reuse decisions
- cleanup behavior is retention-driven and no longer implicit

## Required downstream actions

Downstream consumers should update their deployments and integrations as
follows.

### 1. Use `center_key` for machine-to-machine traffic

Update API clients, frontend writes, automation, and ingest payloads to send
`center_key` instead of mutable display names.

Human-facing UIs may still display center names, but machine-facing payloads
should no longer rely on `center`.

### 2. Set deployment role explicitly

For shared multi-center deployments, set:

```bash
ENDOREG_DEPLOYMENT_ROLE=central_hub
```

Supported roles:

- `central_hub`
- `site_node`
- `standalone`

When role is `central_hub`:

- API uploads must be authenticated
- API uploads must declare `center_key`
- API uploads do not fall back to the default center
- SQLite is rejected in production settings
- transfer API is enabled and must run with secure transport plus mTLS

Watcher ingestion remains supported in hub deployments and keeps trusted
local-drop behavior.

### 3. Configure production proxy HTTPS and mTLS headers

Production deployments behind a TLS-terminating proxy must make the proxy HTTPS
signal explicit:

```bash
DJANGO_SECURE_PROXY_SSL_HEADER_NAME=HTTP_X_FORWARDED_PROTO
DJANGO_SECURE_PROXY_SSL_HEADER_VALUE=https
```

For non-hub production deployments that require a TLS-terminating proxy, set
this fail-closed guard as well:

```bash
DJANGO_REQUIRE_SECURE_PROXY_SSL_HEADER=true
```

Central hub production settings require the proxy HTTPS header because transfer
security depends on `request.is_secure()` before node authentication runs.
Hub transfer deployments must also keep mTLS required:

```bash
ENDOREG_HUB_TRANSFER_REQUIRE_MTLS=true
ENDOREG_HUB_TRANSFER_MTLS_META_KEY=HTTP_X_CLIENT_CERT_VERIFIED
ENDOREG_HUB_TRANSFER_MTLS_META_VALUE=SUCCESS
```

The proxy must:

- strip inbound client-supplied `X-Forwarded-Proto`
- strip inbound client-supplied `X-Client-Cert-Verified`
- set `X-Forwarded-Proto: https` only for HTTPS requests
- set `X-Client-Cert-Verified: SUCCESS` only after successful client
  certificate verification
- leave the mTLS attestation header absent or non-successful when client
  certificate verification fails

Do not expose the Django process directly to untrusted clients when these
headers are trusted.

### 4. Keep storage inside the protected runtime root

The package expects a protected runtime boundary rooted at:

```bash
LX_ANNOTATE_ENCRYPTED_DATA_DIR=/path/to/protected/root
```

Both of these must resolve inside that protected root:

- `STORAGE_DIR`
- `IO_DIR`

Downstream deployments should not point ingest, storage, or workflow paths
outside the protected runtime root.

### 5. Run the package migrations

This upgrade includes schema and lifecycle behavior that depend on current
migrations, including upload-job storage policy and content-hash metadata.

Run the host-project migration flow after upgrading the package.

## Behavioral differences to expect

### Patient and center API contract

- core patient writes are now `center_key`-first
- center serializers expose `center_key`
- downstream apps should stop translating between name-based and key-based
  contracts at the edge

### Upload behavior

- repeated uploads may now reuse an existing `UploadJob` based on content hash
  and center scope
- watcher re-drops of identical content should not create duplicate effective
  ingest records

### Cleanup behavior

- `preserve_source` uploads remain retained after successful completion
- `delete_after_success` uploads become cleanup-eligible after successful
  completion
- cleanup workers and reconciliation logic may remove stale `.tmp` and `.part`
  artifacts

Downstream operators should not assume every uploaded source artifact is kept
forever after successful processing.

## Recommended downstream verification

Before promoting an upgrade, verify the following in the host environment:

1. package migrations apply cleanly
2. patient create/update flows succeed with `center_key`
3. authenticated API upload succeeds with a valid `center_key`
4. API upload fails in hub mode when authentication or `center_key` is missing
5. watcher ingest still works for the local trusted drop zone
6. upload-job status and media-read endpoints respect center scope
7. cleanup jobs do not delete retained source artifacts unexpectedly

## What downstream projects can remove

If a host project previously patched `endoreg_db` to add:

- `center_key` serializer behavior
- hub-mode upload gating
- center-scoped media-read guards
- transfer endpoint gating

those overrides should now be reviewed and removed where they duplicate core
package behavior.

## Related docs

- `setup/CONFIGURATION_GUIDE.md`
- `README.md`
- `changelogs/changelog-0.8.1.md`
