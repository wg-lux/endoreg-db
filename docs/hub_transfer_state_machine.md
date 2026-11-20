# Outbound Hub Transfer State Machine

An outbound hub transfer moves anonymized processed media from a site node to the
central hub. Its progress is stored on `OutboundHubTransferJob` as a status
(`local_status`) and, for failed transfers, a failure category (`failure_class`).
This document describes how those two fields change. Scope and readiness
evidence live only in
[`feature-tracking/StateMachineConformance.yml`](../feature-tracking/StateMachineConformance.yml)
(criterion `native_vocabulary_single_source`).

## Design in one paragraph

Every change is one **event** applied to the current status. A pure Rust function,
[`reduce_hub_transfer_event`](../rust/endoreg_rust_backend/src/hub_transfer_state.rs),
decides the result. One Python function,
[`apply_outbound_transfer_event`](../endoreg_db/services/hub/outbound_transfer_state.py),
is the only code that writes the status fields: it locks the database row, reloads
it, asks Rust, and assigns the result. Workers, reconciliation, eligibility checks
and operator actions all go through it.

```
caller ──► apply_outbound_transfer_event(job, event)
             1. SELECT ... FOR UPDATE and reload the row
             2. reduce_hub_transfer_event(status, failure, event)   (Rust, pure)
                  → apply (new status)  |  ignore  |  reject (ValueError)
             3. on apply: assign status and failure; the caller saves
```

## States

| Status | Meaning |
| --- | --- |
| `marked` | Selected for export; not yet dispatched. |
| `queued` | Waiting for a worker. |
| `registering` | Worker is registering the transfer with the hub. |
| `awaiting_media` | Hub accepted the registration and waits for the media. |
| `uploading` | Worker is streaming the encrypted media. |
| `completed` | Hub applied the transfer with a validated receipt. Final. |
| `failed` | Stopped with a failure category (see below). |

`registering`, `awaiting_media` and `uploading` are the **in-flight** states.

Failure categories: `transient_retry` (retryable), `configuration_rejection`,
`authorization_denial` and `integrity_inconsistency` (not retryable). A category
exists exactly when the status is `failed`; an empty database value means "no
failure". Historical failed rows may lack a category.

## Events and transitions

Each event has one of three outcomes. **Apply** returns a new status for the
caller to save. **Ignore** means the event no longer fits the current status, for
example a duplicate or late event, so the caller must not write. **Reject**
raises `ValueError` for input that indicates an integrity problem.

`completed` ignores every event. For all other statuses:

| Event | Applies when | Result | Otherwise |
| --- | --- | --- | --- |
| `queue` | `marked` | `queued` | ignore |
| `retry` (automatic) | `failed` with `transient_retry` or no category | `queued` | ignore |
| `operator_retry` | `failed` with any category | `queued` | ignore |
| `redispatch` | `queued` | `queued` (refreshes the dispatch) | ignore |
| `start_registration` | `queued` or in-flight | `registering` | ignore |
| `start_upload` | `awaiting_media` | `uploading` | ignore |
| `ineligible` | `queued` or in-flight | `failed` / `configuration_rejection` | ignore |
| `fail` (category) | any status except as below | `failed` / that category | see below |
| `acknowledge` (hub status) | see below | | |

**`fail` on an already failed transfer.** The same category again is ignored. A
`transient_retry` failure (or one without a category) can be replaced by a new
category. Any other failure is kept, so the first non-retryable failure stands.

**`acknowledge`** carries the status reported by the hub:

| Current status | Hub status | Result |
| --- | --- | --- |
| `marked` | any | reject: the transfer was never dispatched |
| `queued`, `failed` (superseded attempt) | `applied` | `completed` |
| `queued`, `failed` (superseded attempt) | any other | ignore |
| in-flight | `applied` | `completed` |
| in-flight | `awaiting_media` | `awaiting_media` |
| in-flight | `failed` | `failed` / `configuration_rejection` |
| in-flight | `inconsistent`, `lost` | `failed` / `integrity_inconsistency` |
| in-flight | `pending`, `claimed`, `running`, `retry_wait` | unchanged, applied so the hub status can be recorded |

Callers validate the hub's envelope receipt before an `applied` acknowledgement is
accepted.

## Guarantees

- **Final completion.** No event leaves `completed`.
- **Idempotency.** Applying the same event to its own result never changes it
  again, so duplicate or retried deliveries are safe.
- **Consistent status and failure.** A failure category is present exactly on
  `failed` transfers.
- **No stale writes.** Because the row is reloaded under a lock before each
  decision, a long-running process cannot overwrite a newer status with an older
  in-memory copy; it receives "ignore" and stops.
- **Fail closed.** Unknown status, category or event strings raise `ValueError`.
  A missing native capability (`hub_transfer_state_v2`) raises `RuntimeError`;
  there is no Python fallback.

## Using it

```python
from django.db import transaction

from endoreg_db.services.hub.outbound_transfer_state import (
    TRANSITION_FIELDS,
    apply_outbound_transfer_event,
)
from endoreg_db.utils.rust_backend import HubTransferEvent

with transaction.atomic():
    if not apply_outbound_transfer_event(job, HubTransferEvent.START_UPLOAD):
        return job  # another writer owns the transfer now; do nothing
    job.media_upload_started_at = timezone.now()
    job.save(update_fields=[*TRANSITION_FIELDS, "media_upload_started_at", "updated_at"])
```

Rules for callers:

- Call inside `transaction.atomic()`; the helper raises otherwise.
- The helper reloads `job`, so set other fields **after** the call.
- Save only when the call returns `True`, and include `TRANSITION_FIELDS`.
- Never assign `local_status` or `failure_class` directly.
- Keep policy decisions in Python, such as retry budgets or staleness thresholds;
  the state machine decides only whether a change is legal.

## Why Rust

- **One vocabulary.** States, hub statuses, failure categories and events are Rust
  enums declared once. The Python mirror enums are compared with them at startup,
  and the application refuses to start on any difference.
- **Exhaustive by construction.** Decisions are a `match` over (status, event). A
  new status or event that is not handled everywhere fails compilation instead of
  falling into a default branch.
- **Invalid input cannot be represented.** Events that need data carry it
  (`Acknowledge(RemoteStatus)`, `Fail(FailureClass)`), and results are built only
  through constructors that keep status and failure consistent.
- **Pure and fully tested.** The function holds no shared or mutable state, so
  concurrent calls cannot interfere. The input space is finite, and the Rust tests
  check every status × event combination against the guarantees above.

Database row locks provide the serialization; Rust provides the rules. Neither
replaces the other.

## Limits

- Saving only on apply is a caller convention checked by tests and review, not by
  the compiler.
- The per-job worker lock is a file lock on one machine. Status stays correct
  across hosts through row locks, but two hosts could repeat the same network
  request; the hub's transfer-key replay protection absorbs it.
- Sender and native extension are versioned together: deploy an endoreg-db
  release that provides `hub_transfer_state_v2` before restarting workers.
