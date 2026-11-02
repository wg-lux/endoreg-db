# Online rotation of media, identity and Django signing secrets

## Choose your task

For media files, start with the [v1 upgrade and resume procedure](#v1-machines-and-interrupted-media-migrations).
For patient identity or signing-key changes, use the [key rotation guide](key_rotation.md).
For implementation details, see [format versions](#format-versions).
These are separate operations; a media-format upgrade does not require changing
patient identity salts or disk encryption.

Application users do not need to run these commands. Ask the operator when
imports and editing can resume. If media becomes unreadable, report the time,
action and non-sensitive error through your approved support channel; preserve
the existing record and file. Do not send patient data or keys in support logs.

First-time administrators should arrange the production service account,
environment and tested recovery copy with their deployment maintainer before
running the procedure. A development shell is not the production environment.

| Term | Meaning here |
| --- | --- |
| Reader / writer | A web process, worker or tool that reads / creates stored files. |
| Keyring | A private file listing which existing key files may be used. It is not a new key. |
| Active / retiring key | The key used for new writes / an older key retained to read existing data. |
| Preview / apply | Check existing files without rewriting them / explicitly rewrite eligible files. |
| HLS | Segmented video used for streaming; its keys and retained generations also need checking. |
| Lease | A temporary reservation while media is in use; wait for it to finish before retrying. |

## Central identity delivery is disabled

Identity salts remain on their originating node. The runtime rejects injected
`DJANGO_IDENTITY_SALT_BUNDLE` and `DJANGO_IDENTITY_SALT_DELIVERY=secretspec`;
LuxNix central provisioning is disabled. Preserve established local file/keyring
configuration and historical generations. See [identity enrollment](identity_ring_enrollment.md)
and [the authoritative criterion](../feature-tracking/Anonymization.yml).
No automatic key replacement, deletion or migration accompanies this restriction.

## Private manifests

Manifests use YAML with integer `schema_version: 1`; unknown fields are rejected.

| Field or format | Contract |
| --- | --- |
| `active` | Absolute path to a private file containing the new active secret |
| `retiring` | Absolute paths to explicitly permitted historical secrets; the complete manifest is bounded to 64 KiB, never silently truncated |
| `allow_legacy_default_salt` | Identity salt only: `true` permits `default_salt` only in `retiring` |
| `center_id`, `generation` | Optional paired identity metadata: stable center key and positive integer issuer generation; managed clients validate `DJANGO_IDENTITY_CENTER_ID` on every load, with an optional `DJANGO_IDENTITY_SALT_GENERATION` pin |
| Permissions | Regular files without symbolic links, owned by root or the service user, with no group or other permissions |
| Publication | Provision generations in separate immutable files, then replace only the manifest atomically; never put secrets in command arguments, logs or Git |
| Master key | URL-safe Base64 encoding of 16, 24 or 32 random bytes; prefer 32 for new generations |
| Identity salt | One nonempty UTF-8 line; active default values remain prohibited |
| Signing key | One UTF-8 line of at least 32 characters, excluding Django development keys |

| Environment setting | Purpose |
| --- | --- |
| `LX_ANNOTATE_MASTER_KEYRING_FILE` | Media keyring; takes precedence over the private master-key file. Inline material remains test-profile only. |
| `DJANGO_IDENTITY_SALT_KEYRING_FILE` | Identity migration; ordinary `DJANGO_SALT_FILE` remains valid without a manifest |
| `DJANGO_SIGNING_KEYRING_FILE` | `SECRET_KEY` and `SECRET_KEY_FALLBACKS` in `endoreg_db.config.settings.prod` |

## Rollout and commands

Install schema 0081 and additive migration `0082_examiner_identity_generation`
first; neither changes existing hashes. All web, import, worker, maintenance and
external storage readers must support the keyring before new media keys become
active. For planned rotation, distribute the new key as an additional reading
generation while the previous key stays active; swap `active` and `retiring`
atomically only after reader rollout is confirmed.

Legacy `default_salt` migration requires coordinated replacement of all identity
writers. Old writers do not participate in the new database locks and must not
run during hash migration. Compatible readers can continue web/media access.
Configure a valid new active salt and explicitly permit the old `default_salt`
file in `retiring`; keep normal production validation enabled.

Master-key and salt manifests are reread during operation. Identity transactions
use a consistent snapshot. Signing keys load with Django settings, requiring a
coordinated rolling process restart and host settings that use the adapter.

Run in the installed service context, with production settings and the service
user; never apply test settings to production data.

| Operation | Command |
| --- | --- |
| Media preview | `python -m django rotate_storage_secrets --include-hls` |
| Media apply | `python -m django rotate_storage_secrets --include-hls --apply` |
| Identity preview | `python -m django rotate_identity_salt` |
| Identity apply | `python -m django rotate_identity_salt --apply` |
| Deliberate partial identity apply | `python -m django rotate_identity_salt --apply --allow-partial` |

An incomplete identity apply rolls back completely unless `--allow-partial` is
explicit. Partial mode commits independently verified groups but still reports
remaining blockers as failure. Retain a verified encrypted snapshot first.
This requires the updated command: endoreg-db 1.3.8 already committed partial
results without that flag. Add `--review-file` with an absolute private YAML path
when supplying reviewed original identities.

## V1 machines and interrupted media migrations

LXENC v1/v2 identifies the file format; the `LXENC01` prefix and the package's
version number do not identify it. Mixed v1/v2 files are supported by the new
readers. This procedure does not convert or format LUKS disks.

1. Retain a tested encrypted database/media backup and every key needed to read
   it. Record the current active and retiring generations without exposing keys.
   For v1 data, compare against independent expected hashes or an authenticated
   inventory: a successful read alone cannot detect historical whole-chunk loss.
2. Finish or stop any existing rotation job cleanly. Coordinate rollout so old
   readers cannot receive v2 writes: deploy the matching Python package and
   native extension to web, workers, maintenance and other readers, then resume
   writes. If coordinated rollout is unavailable, pause writers for that window.
   Verify the deployed artifact includes the v2 fixes; no minimum published
   release is asserted by these unreleased notes.
3. Configure `LX_ANNOTATE_MASTER_KEYRING_FILE` with the existing active key and
   all required retiring keys using the private manifest contract above. For a
   format-only upgrade, keep the existing key active; no new key is required.
   Confirm every process uses the intended manifest. Do not overwrite key files.
4. In the installed production service environment, as its service user, run:

   Preview first:

   ```sh
   python -m django rotate_storage_secrets --include-hls
   ```

   Run apply only after reviewing the preview and recovery evidence. Preview
   authenticates files; it does not rewrite them or prove all files are v2.
   Keep space for a replacement encrypted file; HLS regeneration may need more.

   ```sh
   python -m django rotate_storage_secrets --include-hls --apply
   ```
5. For an interrupted or partial migration, keep the same intended keyring and
   published files. Review the reported counts and protected `.stage` artifacts;
   do not delete staging blindly or promote it manually. Resolve missing or
   unmanaged files and failed authentication using the verified recovery copy.
   Let active media leases finish, then repeat preview and apply. Already-current
   v2 files remain unchanged; v1 files and retiring-key files are processed.
6. Repeat preview and verify old/new media reads, streaming and HLS. Account for
   replicas and retained backups before approving any key retirement. Keep
   recovery keys for every retained backup. Do not downgrade to v1-only readers
   after v2 writes: code rollback cannot reverse ciphertext or database changes.

For an interrupted host-storage migration, retain its separate LuxNix plan,
state and recovery media and follow the LuxNix operator checklist. Do not rerun
formatting or change disk-encryption mode to repair an application read failure.

## Read the media migration result

Read the printed counts and final error or success message before deciding what
to do next. An incomplete run can still have safely migrated some files.

| Result | Next action |
| --- | --- |
| `authenticated` / `rotated` | Files checked / replaced in this run. Zero replacements can be normal on a repeat run; preview alone does not establish v2 completion. |
| `deferred` | Let playback or other active work finish, then retry. |
| `failed` or `missing_registered_files` | Have the operator investigate the named failure or missing file and its recovery copy. Do not generate a new key. |
| `unmanaged_encrypted` or `staging_files_pending_review` | Ask the storage maintainer to identify ownership and review retained files; do not delete or promote them manually. |
| `outside_application_encryption` | Have the maintainer confirm the separate filesystem protection and intended storage policy for these files. |
| `hls_pending` or `historical_hls_keys_pending_retirement` | Review streaming generations separately before retiring keys, even if the file check succeeds. |

Completion requires a successful repeat preview, working old/new media reads,
and review of HLS, replicas and backups. A successful file command alone is not
permission to delete recovery material. For escalation, retain the exact command,
installed version, counts and non-secret error; keep credentials and clinical
data out of shared diagnostics.

## Media replacement contract

- Only registered LXENC01 files inside protected storage are replaced automatically.
  Unassigned encrypted files make the result incomplete.
- Each video replacement holds an exclusive renewed media lease. Playback and
  segment edits defer replacement; rerunning resumes work. New access to that
  video may wait, while other videos remain available without global downtime.
- Every chunk is authenticated. Replacement uses a fresh file key and nonces;
  plaintext remains bounded in memory. Compare complete plaintext length and
  SHA-256 before publication, plus the known database hash for videos. Other
  files need an independent inventory reference to detect prior whole-chunk loss.
- Preserve the source until verification. Stage beside it on the same protected
  filesystem, with capacity for at least one additional encrypted file. Replace
  the published path atomically; open readers retain their file generation.
- Storage range readers keep one open file descriptor throughout each response.
  A killed process may leave encrypted `.stage` files; inspect these before
  retiring keys. No automatic
  destructive cleanup is implied.
- HTTP Live Streaming (HLS) receives new keys and segments through its validated
  generation workflow. This may transcode and has no constant-time guarantee;
  older derivatives remain subject to leases and cleanup rules.
- MPEG-4 Part 14 (MP4) files without LXENC01 rely on filesystem encryption.
  Rotating a Linux Unified Key Setup (LUKS) volume key is a separate LuxNix/storage
  operation.

## Identity migration contract

`services.secret_rotation.migration.rotate_identity_salt` is the typed whole-run
entrypoint shared by the management command. It fixes the keyring snapshot and
PostgreSQL writer lock for the complete operation. `--expected-active-fingerprint`
binds a preview/apply to the independently checked SHA-256 salt fingerprint.
`--compromised` requires this binding and independently reviewed original sources
for every affected metadata row, including rows whose demographics remain present.
Compromised recovery forbids `--allow-partial`. These checks validate the supplied
review evidence structurally; operators remain responsible for authenticating its
clinical source independently of the compromised hash. Review manifests belong
inside the LuxNix encrypted storage boundary, with the existing private-file checks.

Historical identity salts are retained at the source for patient reidentification;
they are neither request credentials nor media-decryption keys. Ordinary source
recognition may preserve an established historical patient hash and clinical links
when identifiers and examination dates were erased. It authenticates incoming
originals using stored identity fingerprints without restoring erased fields.
Unknown generations and ambiguous matches require review. Historical patient hashes
are retained as center-scoped aliases in the same transaction before explicit
migration changes the canonical hash. Center-scoped patient lookup resolves these
aliases and rejects conflicts. This does not yet extend to all downstream consumers:
hub reimport and the examination serializer still require coordinated alias-aware
resolution; historical examination hashes and exported references need separate
compatibility acceptance. These requirements remain tracked in
`Anonymization.yml`, criterion `compromised_identity_salt_migration`.
New patient identities
use the active salt. New examinations for historical patients use the active salt;
explicit reviewed group migration authenticates examination hashes across the
retained generations. No operation deletes historical key material.


The summary distinguishes `applied` and `rolled_back`. `patient_groups` and
`metadata_rows` count independently verified groups/rows even if the enclosing
transaction subsequently rolls back. PostgreSQL transaction locks coordinate
migration and new writers. Patient/examination primary keys and clinical links
remain stable.

Validate the complete connected patient group before changing it. Missing source
identities, inconsistent fingerprints, collisions or uncovered examinations block
that group. Never reconstruct deleted identifiers from hashes or store them again.

A private review manifest contains `identities` with `sensitive_meta_id`,
`first_name`, `last_name`, `dob`, optional `examination_date`, `reviewed_by` and
`review_reference`, taken from an authorized reviewed original source. Optional
`examiners` contain `examiner_id`, `first_name`, `last_name`, `reviewed_by` and
`review_reference`. Unknown examiner generations block complete retirement.
Source-scoped external patient identities are not rewritten as demographic salt
hashes. Append-only audit records remain unchanged; replicas, exports and historic
hash references need their own coordinated migration.

## Historical gc-02 incident, 2026-09-24

The [recovery thread](codex://threads/01a0d25b-c68b-7282-aec8-23191d1321e8)
and privileged read-only checks established the following historical findings;
this document conversion performs no migration or recovery.

The [activation runbook](https://github.com/wg-lux/luxnix/blob/main/docs/activation-failure-recovery.md)
covers NVIDIA versions and Vault AppRole certificates, not identity recovery;
a causal connection was not established. Stricter private-file validation rejected
previously provisioned mode `0640`; recovery changed provisioning to `0600`.
Validate deployed files in the service context before activating such changes.

Legacy salt must be explicitly retained as a reading generation to authenticate
old hashes. This cannot recover lost originals; pseudonymized patient data is not
an original source. The old command committed groups individually before raising
`CommandError`, so process failure did not mean rollback. The new default
transaction removes that ambiguity; intentional partial migration needs
`--allow-partial`.

Observed runtime: lx-annotate 1.3.5 with endoreg-db 1.3.8, active service and
master-key gate, two identity-salt reading generations including the legacy salt.
The thread migrated four patient groups and one examiner while preserving primary
keys. Its encrypted snapshot remains authoritative; no further restore was run.

| Remaining finding at that time | Count |
| --- | --- |
| Placeholder identity | 11 groups |
| Missing linked examination evidence | 2 groups |
| Incompatible examination evidence | 1 group |
| No authenticated patient match | 2 groups |
| Examiners | 4 |

Linked `SensitiveMeta` and patient hashes agreed. Patient demographics did not
authenticate any of the 25 unstamped metadata rows. `date_start` did not
authenticate three uncovered examinations in two groups; interpreting dates as
Coordinated Universal Time (UTC) did not resolve the two unmatched patient groups.
Preserve old reading generations until authorized originals or reviewed backups
can be privately checked against old hashes. Do not delete hashes, guess links,
or invent identities or salts.

PostgreSQL also reported collation-version drift from 2.40 to 2.42, without an
established connection to identity blockers. Updating the version marker alone
does not repair affected indexes; separate maintenance and reindexing are needed.

## Signing and retirement

Signing rotation always leaves `SECRET_KEY_FALLBACKS` empty, including planned
rotation. Coordinate consumer reload and invalidate previous sessions and signed
tokens. Historical manifest entries never authorize old signatures. `configured_signing_keys` in
`endoreg_db.config.secret_keyring` validates keyring, single-file and inline sources
consistently. Other host settings must explicitly use it; an environment entry
alone does not change them.

No command deletes old keys or certifies complete fleet retirement. Repeated
inventory must explain missing/broken files, active leases, staging leftovers,
unmigrated identities and old HLS derivatives. Check backups, replicas, paused
workers and external readers separately before removal. After new writes,
rolling back to software without keyring support is unsafe.

## Secret resolution and encrypted-format boundaries

`read_private_file` and `read_secret_line` require absolute paths, private regular
files, ownership checks and at most 4096 bytes. One final line feed or carriage
return plus line feed is allowed; other whitespace and extra lines are not trimmed.
Single-file and keyring identity/signing inputs share validators. Master material
is strictly Base64-decoded in both paths.

Production media keys come from a private master-keyring manifest or a private
master-key file. Inline `LX_ANNOTATE_MASTER_KEY` material is accepted only in an
explicit test settings profile; production mode rejects it even if a valid file
or keyring is also configured. Running under pytest alone does not authorize
inline keys, and production settings validate this restriction at startup.
Within an explicit test profile, source precedence remains master keyring, inline
master, master file. Identity precedence remains keyring, salt file, optional
inline salt; signing precedence remains keyring, signing file, inline signing
key. Production signing keys require 32 characters and cannot start with
`django-insecure-`. The health check fingerprints `settings.SECRET_KEY`; replacing
a file changes that only after settings reload. Missing production signing keys
also fail under pytest; development/test profiles retain their explicit test keys.
Keyrings reject duplicate fields and YAML merges, with no implicit conversion of
schema version or legacy permission. An identity snapshot remains stable even
without a keyring; a newly provisioned manifest appears only outside that snapshot.

Historical Radon measurements were `load_keyring` D/22 to B/10,
`load_identity_salt` C/11 to B/7, `load_master_key` B/6 to A/1 after centralizing
resolution in `configured_master_keys` A/5, and `build_chunk_index` A/5 to A/4.
Low complexity alone does not prove equivalent typed boundaries.

`LXENC01` plus newline identifies format, not integrity or a secret.
`utils.rust_backend.encryption_status` returns typed `encrypted`/`plaintext` results;
the boolean adapter delegates to it. Missing native support, invalid results,
nonregular files and read errors raise without a Python probe fallback. Short
reads still fill the prefix; nonblocking probe opens prevent named pipes from
blocking a worker. Parsers must still validate headers and authenticate chunks.

Python, native range and native hash readers share `encrypted_format.rs`: headers
are at most 64 KiB, chunk sizes are positive integers, version is exactly 1 or 2
with its matching algorithm, each version's fields are known, unique and complete,
Base64 is canonical URL-safe, and key/nonce/identifier lengths are fixed. Extra or
coerced values fail. Complete readers authenticate each bounded chunk; only the
final chunk may be shorter. Incomplete or unbounded length fields fail before any
chunk is decrypted. Python fills short record reads; indexing validates declared
geometry and file bounds. Even empty-file size queries authenticate the wrapped key.

### Format versions

Backward compatibility means the updated runtime reads existing v1 files without
rewriting them. It does not mean v1-only software can read v2 files. Install the
Python package and native extension together; incompatible native artifacts are
rejected explicitly before a new file is written. The native six-field header
parser API remains available; extended metadata uses the same underlying parser.

Storage writes, repair and rotation share one Python encoder. Python streaming
and native range/hash readers use the Rust header and chunk contract, including
chunk bounds, nonces and authenticated data. Their I/O loops and cryptographic
library calls remain separate and are checked with common v1/v2 fixtures. HLS
key wrapping and transfer envelopes are different protocols, not alternate LXENC
file implementations.

Writers, including rotation replacements, emit version 2 (`AESGCM-chunked-v2`).
Version 1 remains readable. To upgrade registered legacy files, run the media
preview, then media apply above; keep an independently verified encrypted backup
and its keys. Apply upgrades v1 even with an unchanged active key. Rerun after an
interruption; verified v2 files using the active key remain unchanged. Deploy v2
readers to every consumer before applying; a software rollback still needs v2
reading support once v2 files have been written.

| Property | Version 1 | Version 2 |
| --- | --- | --- |
| End of file | Not authenticated | Every chunk's associated data records whether it is final; an empty file is one empty terminal chunk |
| Wrapping-key selection | Trial decryption with each configured generation | `key_id`, a domain-separated SHA-256 prefix of the master key, selects exactly one generation |
| Wrapped data key | Constant associated data | Bound to `key_id` and a random 128-bit `file_id` |

Version 2 therefore detects removed or appended whole chunks and a header without
its terminal chunk in every complete reader. `EncryptedStorage` authenticates the
terminal chunk when it caches a file's layout. Python and native range readers
authenticate that terminal chunk before returning any requested bytes, including
early ranges. Storage responses verify and read the same open file generation.

Version 1 does not authenticate a final total length. Removing whole trailing
chunks requires an independent expected plaintext hash or inventory reference to
detect.

`file_id` is authenticated but not yet stored by models. Swapping complete
encrypted files between records therefore stays undetected until a record stores
and compares the expected `file_id`. Moving a file does not invalidate it: no
path is bound, because ingest and quarantine move protected payloads.

The Python adapter resolves `configured_master_keys` once per operation or batch
and passes active and explicitly retiring keys as bytes to
`rust/endoreg_rust_backend/src/file_identity.rs`. Rust reads no key files or key
environment values. All three key lengths use the native range reader's crypto
routine. Direct native encrypted reads require explicit `master_keys`; plaintext
remains readable without them. Update native extension and Python adapter together.
Stored size/modification time describe ciphertext, while the digest describes
authenticated plaintext. Single and batch readers check file identity and metadata
before and after reads.

References: [Django signing fallbacks](https://docs.djangoproject.com/en/5.2/ref/settings/#secret-key-fallbacks),
[authenticated encryption](https://cryptography.io/en/latest/hazmat/primitives/aead/).
