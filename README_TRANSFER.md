# Video Transfer: gc-08 → gs-02 (metadata-first, portable identifiers)

Purpose
- Exact, step-by-step transfer protocol for moving a VideoFile from gc-08 to gs-02.
- Emphasizes portable identifiers (uuid / hashes / transfer_key) and forbids sending local numeric DB ids.

Glossary
- gc-08 — source node (developer machine, local SQLite)
- gs-02 — target hub (server, Postgres)
- transfer_job — server-side record of the incoming transfer
- metadata-only — send JSON metadata (no media file)
- full-transfer — metadata + upload of media
- portable identifier — uuid, video_hash, transfer_key
- forbidden field — local DB primary key (e.g. "id": 3)

Preconditions
- SSH / HTTP connectivity between gc-08 and gs-02 (tunnel if required).
- Node credentials available: `X-Network-Node-Key`, `X-Network-Node-Secret`.
- Source video already anonymized.
- Sender uses serializer that does NOT include local numeric `id` fields.

Quick one-line flow
1. gc-08: load VideoFile(id=3) locally → 2. build portable metadata payload (no `id`) → 3. POST /api/media/hub/transfers/ to gs-02 → 4. gs-02 validates → 5. gs-02 creates transfer_job and creates/updates local video metadata → 6. (optionally) upload media to /api/media/hub/transfers/<transfer_key>/media/.

Detailed step-by-step

1) Prepare on gc-08 (local lookup only)
- Use the numeric id only to locate the source record on gc-08.
- Gather portable fields: `uuid`, `video_hash`, `processed_video_hash`, `anonymized` flag, `metadata` (duration, frames, checksums), and provenance.
- Do NOT include local numeric DB primary keys (`id`) in the payload.

2) Build metadata payload (snake_case keys only)
- Required top-level fields:
  - `transfer_key`
  - `source_node_key`
  - `target_node_key`
  - `resource_kind`: `"video"`
  - `resource_rows`: dict with `video_file` entry containing portable identifiers
- Example (correct):
```json
{
  "transfer_key": "tx-20260716-abc123",
  "source_node_key": "gc_08_dev",
  "target_node_key": "gs_02_dev",
  "resource_kind": "video",
  "resource_rows": {
    "video_file": {
      "uuid": "8a6f3c4e-...",
      "video_hash": "sha256:abcd...",
      "processed_video_hash": "sha256:ef01...",
      "anonymized": true,
      "metadata": { "duration_sec": 42, "frames": 1234 }
    }
  }
}
```
- Example (incorrect — DO NOT send):
```json
{
  "resource_rows": {
    "video_file": { "id": 3, "uuid": "8a6f3c4e-..." }
  }
}
```

3) Send metadata to gs-02
- Endpoint: `POST /api/media/hub/transfers/`
- Headers:
  - `X-Network-Node-Key: <node-key>`
  - `X-Network-Node-Secret: <node-secret>`
- Transport: TLS or SSH tunnel as required by deployment.
- Example curl (metadata-only):
```bash
curl -X POST https://gs-02.example.local/api/media/hub/transfers/ \
  -H "Content-Type: application/json" \
  -H "X-Network-Node-Key: ${NODE_KEY}" \
  -H "X-Network-Node-Secret: ${NODE_SECRET}" \
  -d @metadata.json
```

4) Server-side validation (gs-02)
- Verify transfer API enabled.
- Verify node credentials.
- Verify `anonymized: true`.
- Verify transport security policy (TLS/mTLS/tunnel).
- Validate JSON schema and ensure forbidden fields (e.g. `video_file.id`) are absent.
- On failure: return explicit validation error (do not retry blindly).

5) On valid metadata: create `transfer_job`
- Server persists transfer_job (transfer_key, nodes, resource_kind, resource_rows, policy, status).
- Server then creates or updates local `VideoFile` records using portable identifiers (uuid/hash). New local numeric ids are assigned by gs-02 DB.

6A) metadata-only mode
- Sequence: metadata POST → server creates transfer_job → server creates/updates metadata-only records → DONE.
- No media file is uploaded.

6B) full-transfer (two-phase recommended)
- Phase 1: POST metadata (as above).
- Phase 2: upload media file for that transfer:
  - Endpoint: `POST /api/media/hub/transfers/<transfer_key>/media/`
  - Include `media_role=processed` (or allowed role).
  - Server stores the file using server storage backend and updates transfer_job status.

7) Storage semantics on gs-02
- Server stores files using gs-02 storage backend (encrypted/local/cloud).
- Paths/names are generated or staged by server logic — do not rely on source filesystem paths.
- Implementations use `endoreg_db` storage helpers; receivers must not persist the sender's absolute source paths.

Sender-side minimal fix (Python)
- Ensure serializer does not emit local `id`. Remove before sending:
```python
# remove local id before sending metadata
payload = build_transfer_payload(video_obj)  # dict
video_row = payload.get("resource_rows", {}).get("video_file", {})
video_row.pop("id", None)  # ensure no local DB id is transmitted
# send payload to server
```

Common validation errors and remedies
- `forbidden field: video_file.id` → remove `id` and resend.
- `anonymization required` → run anonymization pipeline before transfer.
- `invalid node credentials` → verify node key/secret and node permission.

Safety checklist before any write on gs-02
- Confirm only portable ids are present.
- Confirm `anonymized: true`.
- Confirm transport is secure (tunnel/TLS/mTLS).
- Confirm server returned successful validation for metadata-only phase before uploading media or performing destructive actions.

Short summary
- Use numeric id only to locate the source locally; never transmit it.
- Send portable identifiers (uuid, hashes, transfer_key); let gs-02 create local ids and maintain transfer_job provenance.

