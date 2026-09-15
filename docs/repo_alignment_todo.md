# Repository Alignment TODO

> Status tracking was migrated to `feature-tracking/StorageSecurity.yml`. This
> document is retained as architecture context and must not carry an independent
> completion status.

## Goal

Re-align `endoreg-db`, `lx-annotate`, and LuxNix so the protected-media model is consistent again:

- no operator-facing "encryption off" mode
- no direct public serving of protected media
- one coherent storage contract across repositories
- filesystem-encrypted streaming for video where required
- application-layer encrypted storage for managed non-video payloads

## Current Divergence

- `lx-annotate` has a real encrypted Django storage backend (`EncryptedStorage`).
- LuxNix exports `LX_ANNOTATE_USE_ENCRYPTED_STORAGE=1` and assumes encrypted storage is enabled.
- `endoreg-db` does not currently appear to wire the same encrypted storage backend.
- `endoreg-db` still exposes `/media/` serving paths that are probably incompatible with the intended protected-media model.
- `LX_ANNOTATE_USE_ENCRYPTED_STORAGE` still exists as a toggle even though unsupported deployments with `=0` are no longer valid.

## Target Architecture

- `LX_ANNOTATE_ENCRYPTED_DATA_DIR` remains the canonical protected runtime root.
- Application-layer encrypted Django storage is always enabled for managed non-video payloads.
- Raw and processed videos use the filesystem-encrypted streamable path where operationally required.
- Protected binaries are only served through authenticated/authorized API endpoints or Nginx `X-Accel-Redirect`.
- `/media/` is never used for protected media in production.

## TODO

### 1. Remove Unsupported Encryption Toggle Semantics

- Remove `LX_ANNOTATE_USE_ENCRYPTED_STORAGE` as an operator-facing feature flag from:
  - `lx-annotate`
  - LuxNix
  - secret specs
  - deployment docs
- Replace toggle logic with a startup invariant:
  - encrypted storage must be enabled
  - master key env or file must be present
  - startup fails closed otherwise
- Keep compatibility shims only if strictly necessary for migration, and mark them deprecated with a removal date.

### 2. Standardize Shared Storage Contract

- Decide and document the exact split:
  - videos: filesystem-encrypted streamable path
  - reports/documents/managed payloads/sidecars/manifests: encrypted Django storage
- Write one cross-repo contract document and make all three repos reference it.
- Remove misleading naming where "encrypted" currently only means "inside a protected path" rather than ciphertext-at-rest.

### 3. Port / Share Encrypted Storage in `endoreg-db`

- Add the `EncryptedStorage` equivalent to `endoreg-db`, or extract a shared package used by both repos.
- Configure Django default storage in `endoreg-db` to use encrypted storage for managed non-video payloads.
- Audit all direct `FileSystemStorage` usage in `endoreg-db`, especially:
  - [`endoreg_db/models/utils.py`](../endoreg_db/models/utils.py)
- Remove hardcoded storage instantiations that bypass the configured default storage backend.
- Ensure all affected models and file operations remain compatible with:
  - chunked reads
  - encrypted `open()`
  - range reads where needed
  - atomic writes

### 4. Lock Down Protected Media Delivery

- Remove direct protected-media serving via `/media/` in `endoreg-db` production paths.
- Audit and patch:
  - [`endoreg_db/root_urls.py`](../endoreg_db/root_urls.py)
  - any other `static(..., document_root=settings.MEDIA_ROOT)` usage
- Ensure protected binaries are only accessible through:
  - authenticated Django API endpoints
  - center-scoped authorization checks
  - Nginx `X-Accel-Redirect` for protected roots where enabled
- Keep public static assets separate from protected media at the settings and routing level.

### 5. Reconcile Video Storage Behavior

- Keep video optimized for filesystem-level encrypted streaming.
- Document exactly what `storage_mode` means in `endoreg-db`:
  - `app_encrypted`
  - `fs_encrypted_streamable`
- Rename modes if necessary so they reflect actual behavior instead of historical assumptions.
- Ensure stream readiness failures remain fail-closed and never expose raw storage paths.
- Verify streamable artifacts live only under the protected media root and are never public-mounted.

### 6. Audit Report / PDF Storage and Delivery

- Decide whether reports remain encrypted via Django storage or may be exposed as plaintext files inside a protected filesystem root.
- Align `ReportStreamView` and related code with the chosen model.
- If reports are encrypted at rest:
  - use encrypted storage reads consistently
  - disallow plaintext path fallbacks except tightly controlled migration/repair tools

### 7. Migration and Repair Strategy

- Inventory all managed payload classes in both repos.
- Add or align repair commands for plaintext drift.
- Add one-time migration tooling for `endoreg-db` if legacy plaintext managed files exist.
- Ensure migration tooling:
  - uses typed wrappers
  - preserves hashes and metadata
  - emits structured logs
  - fails loudly on partial state

### 8. Runtime Verification and Health Checks

- Add startup/system health assertions in `endoreg-db` equivalent to `lx-annotate`:
  - encrypted storage backend active where required
  - protected root configured
  - master key present where required
  - protected media root not publicly mounted
- Add verification commands/tests for:
  - ciphertext actually written to disk
  - transparent storage round-trip
  - no plaintext probe leakage
  - no `/media/` exposure for protected files

### 9. Test Suite Alignment

- Add cross-repo tests for the intended contract:
  - encrypted storage always active
  - disabling it is rejected
  - protected media never served directly
  - stream endpoints require authorization
  - streamable artifact absence returns controlled errors
- Remove or rewrite tests that assume WhiteNoise/Django `/media/` access for protected video.
- Add regression tests for path/config drift under env overrides and service wrappers.

### 10. Documentation Cleanup

- Update `README`, deployment notes, and secretspec descriptions to remove ambiguity.
- Explicitly distinguish:
  - protected runtime root
  - filesystem encryption
  - application-layer encrypted storage
  - protected streaming via Nginx
- Add a short decision record explaining why videos are handled differently from other managed payloads.

## Open Decisions

- Should `endoreg-db` import the exact `EncryptedStorage` implementation from a shared package, or copy it temporarily and converge later?
- Should reports follow the encrypted-storage model like other non-video payloads, or remain plaintext on protected filesystem storage for operational reasons?
- Should `storage_mode` naming be changed now, or only after the backend alignment lands?

## Recommended Execution Order

1. Remove `/media/` protected-media serving in `endoreg-db`.
2. Make encrypted storage mandatory in `lx-annotate` and LuxNix.
3. Port/shared-package the encrypted storage backend into `endoreg-db`.
4. Eliminate hardcoded `FileSystemStorage` bypasses in `endoreg-db`.
5. Migrate/repair legacy plaintext managed payloads.
6. Add runtime verification and regression tests.
7. Update docs and remove compatibility language.

## Net Minimal Pass

The smallest safe pass that materially reduces risk without forcing the full
storage-backend convergence is:

1. Remove direct public protected-media serving in `endoreg-db`.
2. Stop generating direct storage URLs for protected payloads.
3. Make encrypted storage mandatory in `lx-annotate` and LuxNix.
4. Add startup/health assertions that fail closed when the protected-media
   contract is violated.

### Why this is the minimal pass

- It closes the largest exposure first: protected files being reachable outside
  authenticated API endpoints and Nginx protected routing.
- It does not require immediate porting of `EncryptedStorage` into
  `endoreg-db`, which is a larger integration/migration task.
- It aligns runtime behavior across environments before deeper backend work.
- It preserves the intended split where video can remain filesystem-encrypted
  and streamable.

### Included in this pass

- Remove WhiteNoise/Django direct mounting of protected files at legacy public
  paths.
- Normalize protected file URLs to API stream endpoints for:
  - videos
  - PDFs
  - frames
- Keep Nginx `X-Accel-Redirect` as the production delivery path behind
  authenticated API endpoints.
- Convert `LX_ANNOTATE_USE_ENCRYPTED_STORAGE` from a practical toggle into an
  enforced invariant in `lx-annotate` and LuxNix-supported environments.
- Add a health check or startup assertion that rejects:
  - public `/media/` mounting for protected roots
  - protected media roots outside the protected runtime
  - missing encrypted-storage activation in `lx-annotate`

### Explicitly deferred

- Porting or extracting `EncryptedStorage` into `endoreg-db`
- Re-encrypting legacy plaintext managed payloads in `endoreg-db`
- Refactoring every storage call site away from `FileSystemStorage`
- Renaming `storage_mode`

### Success criteria

- Protected media is no longer publicly mounted by default in `endoreg-db`.
- Frontend-facing URLs for protected payloads resolve to authenticated API
  endpoints, not direct file URLs.
- LuxNix and `lx-annotate` no longer support a meaningful "encrypted storage
  off" mode.
- Production-style environments fail closed if the protected-media contract is
  broken.
