# DICOM interoperability: integration contract and runbook

## Purpose and release boundary

`endoreg_db` catalogs pseudonymized Digital Imaging and Communications in
Medicine (DICOM) exports from `lx-anonymizer` using the strictly validated
version 2 manifest contract. Artifacts must already be anonymized, processed
media inside the protected storage boundary. Before any database mutation, a
verifier supplied by the calling storage adapter receives the expected SHA-256
digest and file size and must confirm artifact integrity.

The current implementation is an import and metadata catalog, not a complete
DICOM node. In particular, the following workflows are outside the implemented
and approved scope:

- creating DICOM files from MPEG-4 Part 14 (MP4) or other raw formats;
- C-STORE, C-FIND, C-MOVE, or other DICOM Message Service Element (DIMSE)
  services;
- DICOMweb with STOW-RS, QIDO-RS, or WADO-RS;
- Picture Archiving and Communication System (PACS) routing and automatic
  transfer to external systems;
- Fast Healthcare Interoperability Resources (FHIR) writes or bidirectional
  synchronization; and
- export of raw media, direct patient identifiers, or keys.

Repository tests demonstrate the Video Endoscopic Image Storage Service-Object
Pair (SOP) Class `1.2.840.10008.5.1.4.1.1.77.1.1.1` with Explicit Value
Representation (VR) Little Endian `1.2.840.10008.1.2.1`. The schema accepts
other syntactically valid SOP and transfer-syntax Unique Identifiers (UIDs), but
they are not operationally approved without separate integration evidence.

## Repository and deployment status

The `dicom` feature tracker is marked `done`, and every criterion is recorded as
`verified`. Its operational evidence is an exercise in an isolated, migrated
test database, including failure recovery, idempotent replay, audit evidence,
and version 2 backfill rollback. This verifies the repository contract; it does
not prove that a particular production deployment has enabled the integration,
completed a local backup, or run its rollout and recovery exercises.

## Version 2 manifest contract

Import uses
`import_dicom_export_manifest(patient_examination=..., payload=..., artifact_verifier=...)`.
Required elements are:

- `schema_version: 2`, a Universally Unique Identifier (UUID) as `export_id`, a
  timezone-aware `created_at` value, and `source_system`;
- de-identification evidence with `patient_identity_removed: true` and artifact
  class `anonymized_processed`;
- a successful upstream validation result;
- Study, Series, and SOP Instance UIDs and transfer-syntax UIDs; and
- a relative storage reference, SHA-256 digest, and positive file size for each
  instance.

Unknown fields are forbidden. Absolute paths and `..` segments are rejected.
Direct original identifiers do not fit the contract and cause validation to
fail. The manifest contains metadata and storage references, but no DICOM
payloads or cryptographic keys.

`export_id` identifies an export idempotently. An identical manifest for the
same examination is a successful replay and creates no additional records. A
reused export ID with different content, or Study, Series, or SOP Instance UIDs
that are already assigned, causes an explicit conflict. A database transaction
prevents partial persistence.

## Deployment and migration

Django migration `0046_dicom_interoperability` must be applied before enabling
import. Artifacts remain inside the local encrypted storage boundary. A calling
adapter must resolve the relative `artifact_reference` only within that boundary
and verify its digest and size. The service itself neither moves nor copies
files.

There are no DICOM-specific environment variables and no unsafe fallback. If a
later phase transports data between nodes, the deployment profile's mTLS and
envelope-encryption requirements apply; the master key must never be
transmitted.

### Version and backfill contract

The runtime accepts only manifest version 2. A missing, string-valued, or
unknown `schema_version` is rejected with the supported version before the rest
of the payload is validated. In particular, version 1 is not implicitly added
or inferred because no safe source contract exists for it.

Check existing version 2 JSON records with:

```text
python manage.py backfill_dicom_manifest_v2
```

The default is a read-only dry run. It validates every record, compares
`export_id` with the primary key, and reports how many manifests would require
canonicalization. `--apply` locks affected rows and writes the canonical
manifest, version, source system, and SHA-256 digest together in one database
transaction. One unknown or invalid record aborts the entire cohort; partial
updates are rolled back. Command-facing errors contain a stable safe error code,
while service-level backfill errors identify records only by a hashed reference;
neither exposes manifest content, patient pseudonyms, DICOM UIDs, or artifact
paths in structured logs.

The repository exercise uses
`tests/fixtures/dicom_manifest_v2_existing.json` as a versioned existing record
and covers dry run, apply, complete transaction rollback, and clear rejection of
unknown versions. Run the focused backfill test through:

```text
devenv tasks run quality:type-safety-operational
```

### Rollout, compatibility period, and rollback

Before `--apply`, create an encrypted database backup of the affected
`DicomExportJob` rows according to the approved database runbook. The dry run
must finish without validation errors. Run it in a maintenance window and abort
on any version or validation error, an unexpected update count, or a digest
conflict. Resume import workers only after a successful apply.

The backfill changes only the canonical representation of the same version 2
contract, so a code rollback requires no data reverse migration. If the earlier
JSON representation is nevertheless required, restore only `schema_version`,
`manifest`, and `manifest_sha256` from the encrypted backup; do not copy
artifacts or keys.

Version 2 is the documented compatibility baseline. The policy is to retain
read support for at least twelve months after the first production release of a
later version. Set a concrete version 2 end date only together with the successor
schema, its explicit backfill, and a successful rollback exercise. No version 2
end date is currently defined; later unknown versions continue to fail closed.

## Observability

The `endoreg_db.interoperability.dicom` logger emits structured events:

| Event | Meaning | Expected response |
| --- | --- | --- |
| `dicom.import_completed` | Import fully committed | Confirm the export as processed |
| `dicom.import_replayed` | Identical export already imported | Treat as a successful idempotent retry |
| `dicom.import_rejected` / `invalid_manifest` | Schema or privacy contract violated | Correct the sender payload; do not retry blindly |
| `dicom.import_rejected` / `artifact_integrity_failed` | Digest or size does not match | Isolate the artifact and inspect its source and protected storage |
| `dicom.import_rejected` / `identity_conflict` | Export or DICOM UID conflicts | Do not rewrite a UID; investigate the conflict with the responsible clinical and technical teams |
| `dicom.import_rejected` / `concurrent_identity_conflict` | Concurrent import conflicts | Inspect state, then retry the identical payload |

For correlation, events contain only SHA-256-hashed export and examination
references. Patient pseudonyms, DICOM UIDs, and artifact paths are not emitted
as operational event fields.

Recommended monitoring, which must be configured and verified by each
deployment:

- alert on every increase in `dicom.import_rejected`, grouped by `reason`;
- warn on repeated `artifact_integrity_failed` events from the same source;
- provide a dashboard for `completed`, `replayed`, and `rejected`; and
- periodically confirm that the structured production logging configuration
  captures events in the intended protected log destination.

## Recovery and operational exercise

1. Check migrations and protected-storage availability.
2. Import a known anonymized test export successfully and confirm the
   `dicom.import_completed` event.
3. Send the same manifest again. It must produce `dicom.import_replayed`; Study,
   Series, and Instance records must not be duplicated.
4. Use a test copy with a different digest. The import must fail with
   `artifact_integrity_failed` before any persistence.
5. After repairing the artifact, import exactly the same original manifest
   again. It must succeed completely.
6. Record database counts, structured events, and the calling job status
   together.

Automatic retry is appropriate only for unchanged payloads. Validation and
identity conflicts first require responsible review. Artifacts with integrity
failures must not be marked valid automatically, renamed, or replaced from
untrusted sources.
