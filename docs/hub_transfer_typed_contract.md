# Typed Hub Transfer Contract 3.0

[Deutsche Fassung](hub_transfer_typed_contract.md)

This is the technical integration and migration guide for senders that transfer
anonymized, processed media to a current `endoreg_db` central hub. Completion
status is recorded exclusively in
[`feature-tracking/HubTransfer.yml`](../feature-tracking/HubTransfer.yml).

The guide is intended especially for implementations that still use payload
schema `1.0` or untyped `dict[str, Any]` payloads. Do not weaken the receiver to
accommodate those implementations. Migrate them to shared contract `3.0`
before the first network request.

## Authoritative sources and layer boundaries

| Responsibility | Authoritative source | Purpose |
| --- | --- | --- |
| Cross-repository wire contract | `lx_dtypes.models.contracts.hub_transfer` | Strict frozen Pydantic models, typed return values, and canonical serialization |
| Endoreg persistence boundary | `endoreg_db.schemas.persisted_json` | Validation and canonicalization of `resource_rows` and `processing_snapshot` before JavaScript Object Notation (JSON) persistence |
| Hypertext Transfer Protocol (HTTP) boundary | `endoreg_db.serializers.hub.transfer_job.TransferJobCreateSerializer` | Schema version, node and center ownership, privacy, anonymization state, and hash linkage |
| Persistence | `endoreg_db.models.hub.transfer_job.TransferJob` | Fields, choices, constraints, and repeated JSON validation in `clean()` and `save()` |
| Receiver workflow | `endoreg_db.services.hub.transfers` | Replay, media integrity, atomic storage, state transitions, and acknowledgement |
| Sender workflow | `lx_annotate.hub.hub_export_payloads` and `hub_export_worker` | Payload construction, local validation, mutual Transport Layer Security (mTLS), retry, and acknowledgement validation |

Model new shared fields in `lx_dtypes` first. Endoreg-specific persistence
fields remain in `endoreg_db.schemas`. Request handling does not belong in
Django models, and network or filesystem operations do not belong in Pydantic
models.

## Non-negotiable rules

- `payload_schema_version` is exactly `"3.0"`. Versions `"1.0"` and `"2.0"`
  are rejected.
- The only implemented transfer mode is `metadata_and_processed_media`. Raw
  media is neither registered nor uploaded. Implementation does not imply a
  production-readiness approval; the required criteria in
  `feature-tracking/HubTransfer.yml` remain authoritative.
- Media is eligible only when its anonymization state resolves to `VALIDATED`.
  `ANONYMIZED` or `DONE_PROCESSING_ANONYMIZATION` alone is insufficient.
- `source_center_key` must match the authenticated `NetworkNode.owning_center`.
  A Django user session is neither the source of nor a substitute for center
  scope on these machine-to-machine endpoints.
- Direct identity fields such as names or dates of birth are prohibited.
  `sensitive_meta` contains only `patient_hash` and `examination_hash`, each a
  canonical lowercase 64-character Secure Hash Algorithm 256-bit (SHA-256)
  hexadecimal value.
- Reports contain only `anonymized_text`; `text` is prohibited.
- Recalculate the SHA-256 digest of the exact processed artifact before
  constructing the payload. For videos it appears in
  `video_file.processed_video_hash` and
  `video_state.processed_file_sha256`; for reports it appears in
  `raw_pdf_state.processed_file_sha256`.
- Treat the payload, file, and remote acknowledgement as untrusted input even
  when the channel uses mTLS.
- `NetworkNode.shared_secret` authenticates requests. It is not an encryption
  key. Neither a master key nor raw media may leave the local security
  boundary.

## Why validation happens at two boundaries

The sender validates the complete wire payload with `lx_dtypes` before
disclosing metadata or media. The receiver validates the same contract again
at its HTTP boundary and then canonicalizes the persisted JSON subobjects.
These responsibilities do not compete:

1. `lx_dtypes` prevents an incompatible sender from starting a request.
2. The serializer protects the receiver from stale or manipulated clients and
   resolves local node and center references.
3. `TransferJob.save()` protects direct Object-Relational Mapper (ORM) writes
   and subsequent updates.

The validator return value is the canonical payload. Do not continue passing
the original unvalidated mapping.

```python
from typing import Any, cast

from lx_dtypes.models.contracts import validate_hub_transfer_video_payload
from lx_dtypes.models.contracts.hub_transfer import (
    HubTransferVideoTransferPayloadData,
)

candidate: dict[str, Any] = build_candidate_payload()
payload: HubTransferVideoTransferPayloadData = (
    validate_hub_transfer_video_payload(candidate)
)

# Use only `payload` from this point onward, never `candidate`.
send_json(cast(dict[str, Any], payload))
```

Translate a `ValidationError` into a terminal sender configuration or payload
failure. There is no silent fallback to schema `1.0`, untyped extra fields, or
shared-secret-only transport.

## Common envelope

Video and report transfers use the same top-level fields:

| Field | Meaning |
| --- | --- |
| `transfer_key` | Deterministic transfer identity that remains stable across retries |
| `source_node_key` | Active sender node with role `site_node` |
| `target_node_key` | Active receiver node with role `central_hub` |
| `source_center_key` | Must match the sender node's `owning_center` |
| `resource_kind` | `video` or `report` discriminator |
| `resource_hash` | Domain identity of the source object; must match its resource row |
| `transfer_mode` | `metadata_and_processed_media` in the production path |
| `processing_policy` | Currently `preserve_processing_state` |
| `processing_intent` | Currently `sender_requests_state_preservation` |
| `cleanup_policy` | Conservative default `retain_all` |
| `payload_schema_version` | Literal `3.0` |
| `resource_rows` | Payload discriminated by `resource_kind` |
| `processing_snapshot` | Currently `sender_processing_success: true` |
| `provenance` | Optional anonymized transport provenance without local primary keys |

Local database identifiers, absolute paths, and original filenames are not
portable identities and do not belong in the wire payload.

## Video payload

A minimal processed-video payload for contract `3.0` is:

```json
{
  "transfer_key": "site_a__video__<resource_sha256>__processed_v1",
  "source_node_key": "site_a",
  "target_node_key": "central_hub",
  "source_center_key": "center_a",
  "resource_kind": "video",
  "resource_hash": "<resource_sha256>",
  "transfer_mode": "metadata_and_processed_media",
  "processing_policy": "preserve_processing_state",
  "processing_intent": "sender_requests_state_preservation",
  "cleanup_policy": "retain_all",
  "payload_schema_version": "3.0",
  "resource_rows": {
    "video_file": {
      "video_hash": "<resource_sha256>",
      "processed_video_hash": "<processed_file_sha256>",
      "suffix": ".mp4",
      "fps": 25.0,
      "duration": 60.0,
      "frame_count": 1500,
      "width": 1280,
      "height": 720
    },
    "sensitive_meta": {
      "patient_hash": "<patient_sha256>",
      "examination_hash": "<examination_sha256>"
    },
    "video_state": {
      "processing_started": true,
      "sensitive_meta_processed": true,
      "anonymized": true,
      "anonymization_validated": true,
      "processed_file_sha256": "<processed_file_sha256>"
    },
    "processing_history": {
      "file_hash": "<resource_sha256>",
      "success": true
    },
    "video_segments": [],
    "frame_annotations": [],
    "reports": []
  },
  "processing_snapshot": {
    "sender_processing_success": true
  }
}
```

Every segment has these additional invariants:

- `source_node_key` and `video_hash` match the envelope;
- `end_frame_number_exclusive` is exclusive and greater than
  `start_frame_number`;
- a segment does not exceed the declared `frame_count`;
- the pair of `source_node_key` and `source_segment_id` is unique within the
  payload;
- `model_name` and `model_version` are supplied together and only for exported
  prediction segments;
- presentation timestamps remain authoritative for clinical identity. A
  transfer must not recalculate frame coordinates.

Video storage normalization remains an additional mandatory check. A matching
SHA-256 digest proves integrity, not codec, pixel format, resolution, frame
rate, bitrate, byte budget, or timeline conformance. The authoritative contract
is [`video_storage_normalization.md`](video_storage_normalization.md).

## Report payload

Reports transfer only the anonymized derivative in Portable Document Format
(PDF). A minimal processed-report payload is:

```json
{
  "transfer_key": "site_a__report__<resource_sha256>__processed_v1",
  "source_node_key": "site_a",
  "target_node_key": "central_hub",
  "source_center_key": "center_a",
  "resource_kind": "report",
  "resource_hash": "<resource_sha256>",
  "transfer_mode": "metadata_and_processed_media",
  "processing_policy": "preserve_processing_state",
  "processing_intent": "sender_requests_state_preservation",
  "cleanup_policy": "retain_all",
  "payload_schema_version": "3.0",
  "resource_rows": {
    "raw_pdf_file": {
      "pdf_hash": "<resource_sha256>",
      "anonymized_text": "Anonymized report text"
    },
    "sensitive_meta": {
      "patient_hash": "<patient_sha256>",
      "examination_hash": "<examination_sha256>"
    },
    "raw_pdf_state": {
      "processing_started": true,
      "sensitive_meta_processed": true,
      "anonymized": true,
      "anonymization_validated": true,
      "processed_file_sha256": "<processed_file_sha256>"
    },
    "processing_history": {
      "file_hash": "<resource_sha256>",
      "success": true
    },
    "reports": []
  },
  "processing_snapshot": {
    "sender_processing_success": true
  }
}
```

`pdf_hash` is the domain resource identity. The separate
`processed_file_sha256` identifies the exact bytes uploaded during phase two.
The receiver compares the upload with this digest. The `text` field, direct
patient identity, `raw_meta`, and raw PDF bytes are not part of the transfer
contract.

## mTLS transport type

The current sender uses an explicit frozen transport object:

```python
from dataclasses import dataclass
from typing import TypedDict


class HubTransportRequestKwargs(TypedDict, total=False):
    allow_redirects: bool
    verify: str | bool
    cert: tuple[str, str]


@dataclass(frozen=True)
class HubTransportConfig:
    cert: tuple[str, str] | None
    verify: str | bool
```

The corresponding sender settings are:

```sh
LX_ANNOTATE_HUB_EXPORT_REQUIRE_MTLS=true
LX_ANNOTATE_HUB_EXPORT_CLIENT_CERT_FILE=/run/secrets/hub-client.crt
LX_ANNOTATE_HUB_EXPORT_CLIENT_KEY_FILE=/run/secrets/hub-client.key
LX_ANNOTATE_HUB_EXPORT_CA_FILE=/run/secrets/hub-ca.crt
```

When mTLS is enabled, the certificate and key must both exist and be readable.
An optional Certificate Authority (CA) bundle changes `verify=True` to its
path, never to `False`. Targets must use `https://`. Redirects are disabled so
node credentials cannot be forwarded to another destination.

On the receiver, the proxy contract in
[`deployment_note_hub_contract.md`](deployment_note_hub_contract.md) remains
mandatory: remove client-supplied forwarded and certificate headers, then set
them only after successful proxy verification.

## Two-phase workflow and acknowledgement

1. The sender locks or loads its local outbound job.
2. It calculates the processed-media digest from the current bytes.
3. It builds the payload and validates it with the appropriate
   `validate_hub_transfer_*_payload()` validator.
4. It registers metadata with the same deterministic `transfer_key`, including
   on retries.
5. Only after `awaiting_media` does it upload `media_role=processed`.
6. It retrieves status and validates the acknowledgement against the immutable
   local job.
7. Only `applied` with a completely matching identity permits `completed` or
   local cleanup eligibility.

At minimum, the acknowledgement must match:

- remote transfer identifier and `transfer_key`;
- source node, target node, and source center;
- `resource_kind`, `resource_hash`, and `processed_media_hash`;
- `transfer_mode` and `payload_schema_version`.

Missing or different fields are terminal integrity failures. Do not hide them
behind a new transfer key or an unvalidated retry.

## Migrating `data-transfer-nginx-mtls`

Apply these replacements when porting isolated ideas from an older branch:

| Old approach | Current approach |
| --- | --- |
| Custom `HubTransferClient` in `endoreg_db` | Sender workflow in `lx_annotate.hub.hub_export_worker` |
| `verify_tls: bool` and an untyped keyword-argument dictionary | `HubTransportConfig` and `HubTransportRequestKwargs` with `verify: str | bool` |
| Command-line flags with an optional client certificate | Production profile requires complete mTLS material and fails before a request otherwise |
| Payload schema `1.0` | Strict discriminated `3.0` schema from `lx_dtypes` |
| Continue using `dict[str, Any]` after validation | Replace the original mapping with the validator return value |
| `ANONYMIZED` or `sensitive_meta_processed` is sufficient | Require explicit `anonymization_validated=true` |
| Transmit patient data so the receiver can derive hashes | Transmit only locally derived `patient_hash` and `examination_hash` |
| Report fields `text` and `anonymized_text` | Only `anonymized_text` |
| Report upload without a separate processed digest | Require `raw_pdf_state.processed_file_sha256` |
| Filesystem implementation under `endoreg_db.utils.file_operations` | Mutate through canonical `endoreg_db.utils.filesystem.file_operations`; do not make compatibility imports a new ownership boundary |
| Django session as transfer scope | Authenticated `NetworkNode.owning_center` is the sole machine-to-machine scope |

Do not merge the old branch wholesale. Port only isolated changes which retain
the same types and invariants after rebasing. In particular, do not carry over
old payload builders, raw-media paths, filesystem moves, or session-scope
assumptions.

## Troubleshooting

| Error or status | Cause | Correction |
| --- | --- | --- |
| `Only privacy-preserving hub payload_schema_version '3.0' is accepted` | Stale sender | Upgrade sender and `lx_dtypes` together; do not add a receiver fallback |
| `extra_forbidden` | Stale or directly identifying field | Remove the field or model it in the shared contract first |
| `anonymization_status=... is not eligible` | Explicit validation is incomplete | Complete clinical anonymization validation |
| `processed_file_sha256 is required` | Processed bytes were not hashed locally | Calculate SHA-256 from the exact artifact to be sent |
| `source_center_key must match ... owning center` | Node and center configuration disagree | Correct `NetworkNode.owning_center` and the sender job |
| `inconsistent` during replay | Same key with a different canonical payload | Investigate the existing job; create a new key only for a new domain identity |
| `403` despite a shared secret | HTTPS, mTLS, or node verification is missing | Check certificate, CA, proxy attestation, and node; do not fall back to shared-secret-only transport |

## Verification after migration

In the Endoreg repository:

```sh
.devenv/state/venv/bin/pyright
.devenv/state/venv/bin/pytest \
  tests/views/media/test_hub_transfer_endpoints.py \
  tests/services/test_transfer_job_contract.py -q
```

In the lx-annotate repository:

```sh
.devenv/state/venv/bin/pyright
.devenv/state/venv/bin/pytest tests/hub -q
```

A production-like cross-repository test is also required: valid video and
report transfers, missing certificate, wrong CA, expired certificate, wrong
center, payload `1.0`, raw field, hash mismatch, exact replay, changed replay,
lost acknowledgement, and worker restart.

## Further reading

- [`hub_ingest_operations.md`](hub_ingest_operations.md)
- [`deployment_note_hub_contract.md`](deployment_note_hub_contract.md)
- [`wiki/hub_ingest_current_state.md`](wiki/hub_ingest_current_state.md)
- [`video_storage_normalization.md`](video_storage_normalization.md)
- `/home/admin/dev/lx-annotate/docs/guides/hub-export-workflow.md`
