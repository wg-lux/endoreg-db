# Online rotation of media, identity and Django signing secrets

Coordination source: [LuxNix secret management](https://github.com/wg-lux/luxnix/blob/main/secrets-management.yml).
Scope and readiness: [Anonymization.yml](../feature-tracking/Anonymization.yml),
criterion `online_secret_rotation`. This document describes operations and limits;
it does not provision, publish or delete secrets. A stolen key remains usable
against previously stolen copies. Identity salt produces hashes, Django
`SECRET_KEY` signs sessions and tokens, and media uses independent master and
per-file keys.

## Private manifests

Manifests use YAML with integer `schema_version: 1`; unknown fields are rejected.

| Field or format | Contract |
| --- | --- |
| `active` | Absolute path to a private file containing the new active secret |
| `retiring` | At most eight absolute paths to explicitly permitted previous secrets |
| `allow_legacy_default_salt` | Identity salt only: `true` permits `default_salt` only in `retiring` |
| Permissions | Regular files without symbolic links, owned by root or the service user, with no group or other permissions |
| Publication | Provision generations in separate immutable files, then replace only the manifest atomically; never put secrets in command arguments, logs or Git |
| Master key | URL-safe Base64 encoding of 16, 24 or 32 random bytes; prefer 32 for new generations |
| Identity salt | One nonempty UTF-8 line; active default values remain prohibited |
| Signing key | One UTF-8 line of at least 32 characters, excluding Django development keys |

| Environment setting | Purpose |
| --- | --- |
| `LX_ANNOTATE_MASTER_KEYRING_FILE` | Media keyring; overrides the single master-key configuration while set |
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
- In keyring mode, range readers use the Python reader with a fixed descriptor
  rather than reopening through the native reader. A killed process may leave
  encrypted `.stage` files; inspect these before retiring keys. No automatic
  destructive cleanup is implied.
- HTTP Live Streaming (HLS) receives new keys and segments through its validated
  generation workflow. This may transcode and has no constant-time guarantee;
  older derivatives remain subject to leases and cleanup rules.
- MPEG-4 Part 14 (MP4) files without LXENC01 rely on filesystem encryption.
  Rotating a Linux Unified Key Setup (LUKS) volume key is a separate LuxNix/storage
  operation.

## Identity migration contract

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

Planned rotation may temporarily retain previous signing keys in
`SECRET_KEY_FALLBACKS`. A compromised key must never remain a fallback; invalidate
its sessions and signed tokens. `configured_signing_keys` in
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

Source precedence remains: master keyring, inline master, master file; identity
keyring, salt file, optional inline salt; signing keyring, signing file, inline
signing key. Production signing keys require 32 characters and cannot start with
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
are at most 64 KiB, chunk sizes are positive integers, version is exactly 1, the
algorithm and fields are known and unique, Base64 is canonical URL-safe, and
key/nonce lengths are fixed. Extra or coerced values fail. Complete readers
authenticate each bounded chunk; only the final chunk may be shorter. Incomplete
length fields fail. Python fills short record reads; indexing validates declared
geometry and file bounds. Even empty-file size queries authenticate the wrapped key.

LXENC01 does not authenticate a final total length. Removing whole trailing chunks
requires an independent expected plaintext hash or inventory reference to detect.
Range readers authenticate requested chunks rather than the entire file.

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
