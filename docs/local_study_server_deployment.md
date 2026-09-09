# Local Study Server Deployment

The binding protection scope, quality limits, reviewer steps, and
failure/correction scenario are defined in the
[anonymization and release contract](anonymization_contract.md).

`local_study_server` is a production-settings profile for one encrypted host. It
accepts center-scoped authenticated API uploads, external preanonymized imports,
and explicit promotion of existing managed processed media into an export-ready
state. It is not a federation or transfer role. Availability of the profile in
code does not by itself establish production readiness: the required criteria in
`feature-tracking/StorageSecurity.yml` and `feature-tracking/Anonymization.yml`
remain authoritative.

## Required Profile

Set:

```sh
ENDOREG_DEPLOYMENT_ROLE=local_study_server
DJANGO_DEBUG=false
DB_ENGINE=django.db.backends.postgresql
LX_ANNOTATE_ENCRYPTED_DATA_DIR=/var/lib/lx-annotate/encrypted
STORAGE_DIR=/var/lib/lx-annotate/encrypted/storage
PROTECTED_MEDIA_ROOT=/var/lib/lx-annotate/encrypted/storage
```

Startup fails if the role uses SQLite, debug mode, unauthenticated API defaults,
or protected storage roots outside the encrypted runtime boundary.

Django verifies path shape, not encryption state. The host deployment must also
enforce that `/var/lib/lx-annotate/encrypted` is an encrypted mount, has the
expected owner/group/mode, and is mounted before the service starts. The example
systemd units use `RequiresMountsFor=`; LuxNix/NixOS deployments should express
the equivalent mount and database dependencies in the system configuration.

## Reverse Proxy Deny Rules

Transfer APIs remain disabled in Django and should also be blocked before
requests reach Python:

```nginx
location ^~ /endoreg-api/media/hub/transfers/ {
    return 404;
}

location ^~ /api/media/hub/transfers/ {
    return 404;
}
```

Keep protected media serving on the local host boundary. Do not expose exported
datasets over a public route.

## HLS Manifest URI Contract

Local same-origin HLS playlists are materialized with API-rooted relative URIs,
for example `/endoreg-api/media/videos/{video_id}/hls/key/{key_id}/` and
`/endoreg-api/media/videos/{video_id}/hls/segments/{key_id}/seg_000.ts`.
The committed playlist must not embed a scheme or host, and the local deployment
path serves that file as-is through Django `FileResponse` or nginx
`X-Accel-Redirect`; do not add per-request manifest token rewriting for this
path.

Same-origin local HLS playback does not require CORS headers. If the frontend,
API, and media endpoints are split across domains or subdomains later, HLS
credentialed CORS must be configured with explicit origin allowlisting and must
never emit wildcard `Access-Control-Allow-Origin: *` together with credentials.

Production local HLS segment delivery requires nginx protected-media offload:

```sh
SERVE_WITH_NGINX=true
NGINX_PROTECTED_MEDIA_URL=/protected_media/
```

`NGINX_PROTECTED_MEDIA_URL` must map to the same protected media storage tree
that contains materialized HLS playlists and segments. The Django API performs
authentication and authorization, then returns `X-Accel-Redirect`; nginx serves
the segment bytes from protected storage.

Store materialized HLS segment directories on SSD/NVMe-backed encrypted storage
for concurrent playback. Capacity planning should account for both local NIC
throughput and disk I/O: each active playback consumes sustained read bandwidth
from protected media storage and outbound bandwidth on the host network
interface. Size the host for expected concurrent viewers, HLS bitrate, and
background materialization or audit jobs rather than only total disk capacity.

HLS content keys are wrapped with the Lux Annotate master key. Never commit,
log, bake into container or VM images, or store `LX_ANNOTATE_MASTER_KEY` in
database backups. Prefer `LX_ANNOTATE_MASTER_KEY_FILE` pointing at a mounted
secret or tmpfs file with restricted permissions. The database stores wrapped
HLS content keys and nonces, not plaintext HLS content keys.

Do not use a database migration to rewrite historical key or segment URIs. If an
older materialized playlist contains absolute, stale, or host-bound URIs, handle
it as an operational audit and regeneration task: identify affected playlists,
confirm the processed source artifact is still permitted for outbound streaming,
then run the regeneration command for each affected video ID:

```sh
manage.py materialize_video_hls --video-id <id> --artifact-kind processed --apply --inline --force
```

## Managed Media Promotion

lx-annotate post-validation data is already in managed storage. It must not be
handed back through a watcher that assumes a new imported file.

Promote an existing managed video with:

```http
POST /api/media/videos/{video_id}/mark-ready-for-export/
```

Body:

```json
{
  "center_key": "site-a",
  "processed_file_sha256": "optional 64 lowercase sha256 hex characters"
}
```

The endpoint verifies authenticated access, center scope, `processed_file`
presence, protected managed-storage path, human anonymization validation,
outside-segment removal, current processed-artifact SHA-256, and appends
ready-for-export provenance to the audit ledger before setting:

- `VideoState.ready_for_export`
- `VideoState.ready_for_export_at`
- `VideoState.ready_for_export_by`
- `VideoState.processed_file_sha256`

The promotion endpoint does not accept or trust client-supplied
`validated_by`, `validated_at`, or `human_anonymization_validated`. Those values
come from authenticated server-side validation state and server-side timestamps.

Changing segment annotations or reprocessing media clears readiness and requires
outside-segment removal plus promotion again.

## External Preanonymized Imports

`preanonymized_import` remains a separate import path for genuinely new external
preanonymized media. Raw `video_import` and `report_import` watcher ingestion is
rejected in `local_study_server`.

Each media file requires a same-basename `.json` sidecar with:

```json
{
  "center_key": "site-a",
  "source_system": "external-preanonymized-drop",
  "file_sha256": "64 lowercase sha256 hex characters",
  "human_anonymization_validated": true,
  "validated_by": "operator id",
  "validated_at": "2026-05-06T12:00:00+02:00"
}
```

Extra fields are rejected unless modeled. Hash mismatch, unknown center, unsafe
path, missing sidecar, or missing human validation moves the drop to quarantine.

## Daily Operations

Refresh the audit ledger integrity cache before running health checks. Production
deployments should use the packaged Python environment or fixed venv path, not
`devenv shell`:

```sh
/opt/endoreg-db/venv/bin/python /opt/endoreg-db/manage.py refresh_audit_ledger_integrity --once --fail-on-non-verified
/opt/endoreg-db/venv/bin/python /opt/endoreg-db/manage.py check_system_health --json
```

Quarantine cleanup is approval-gated. The dry run indexes current quarantine
files and reports stale pending-review material. Confirmed deletion only removes
files that have first been explicitly approved for deletion.

```sh
/opt/endoreg-db/venv/bin/python /opt/endoreg-db/manage.py reap_quarantine --older-than-days 30 --dry-run --json
/opt/endoreg-db/venv/bin/python /opt/endoreg-db/manage.py reap_quarantine --older-than-days 30 --approve-stale --decision-reason "retention period elapsed" --json
/opt/endoreg-db/venv/bin/python /opt/endoreg-db/manage.py reap_quarantine --older-than-days 30 --confirm --json
```

Example systemd unit files live in `docs/ops/local_study_server/`. The health
timer writes JSON to journald and exits non-zero on critical issues, including
failed/lost upload jobs, stale quarantine, insufficient free storage, transfer
API exposure, unverified audit ledger integrity, unresolved video/report
anonymization failures, or video processing histories active for more than seven
hours. The latter counters are emitted under
`local_study_server.anonymization_processing`.

V1 monitoring is systemd plus journald. Local mail can be wired by adding an
`OnFailure=` notification unit that invokes `sendmail` with the failed unit
status from `systemctl status`.

## Export Boundary

Training exports default to frames only:

```yaml
export_frames: true
export_videos: false
use_export_flags: true
only_validated: true
center_key: site-a
```

In `local_study_server`, frame image exports are generated from `processed_file`
into an export-scoped generated-frame directory at export time. Existing
canonical managed frame files are not copied unless a future provenance model
proves they were generated from the promoted processed artifact hash.

Use `all_centers: true` only for an explicit privileged operational export.
Exported datasets lack envelope encryption/KMS packaging in v1 and must stay
inside the secure local host boundary unless moved through a secure
administrative process.

Training consumers must de-duplicate or review overlapping identical segment
exports because ICA rows can collide across equivalent segment metadata.
