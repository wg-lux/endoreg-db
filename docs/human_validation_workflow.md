# Human Validation Between Import and Export

Binding requirements and audit evidence reside exclusively in
[ProductionWorkflow.yml](https://www.google.com/search?q=../feature-tracking/ProductionWorkflow.yml).
This document describes the transition contract and potential failure modes;
it does not claim complete enforcement across all export paths.
For video generations, streaming, and deletion, the English
[Video-Runbook](https://www.google.com/search?q=video_storage_normalization.md) applies.

## Identities and Evidence

The content hash of the **imported source** identifies identical documents or
videos. It is preserved after anonymization, redaction, and deletion.
`RawPdfFile.pdf_hash` and `VideoFile.raw_video_hash` are the existing
source identities. The hash of a processed artifact, on the other hand, refers
specifically to its content and must not replace the source identity.
For format conversions, the original input must also be tracked:
`ReportImportService` converts text files into a document prior to the actual
report import. The document hash stored as a result must not be unverified
(blindly) exported as the hash of the original text file. The same contract
must be explicitly verified for tabular and structured report imports.

`SensitiveMeta.patient_hash` designates the patient identity;
`examination_hash` designates the examination. Same patient means
neither same document nor same examination. A new report for the same
patient must therefore not be discarded as a content duplicate.
`calculate_patient_hash()` in `models/metadata/sensitive_meta_logic.py` also
uses center and salt. Cross-center linkability therefore does not follow
solely from matching names and date of birth. The existing hash contract must
not be modified without a versioned migration. Such an identifier
is pseudonymous, not proof of irreversible anonymity.

A sign-off/release must be domain-bound to the verified state:
resource, source identity, patient identity, metadata revision,
artifact hash, optionally segment revision, reviewer, and review timestamp.
This list is a target contract, not an already existing database schema.
After removing direct identifiers, the confirmed identity is preserved;
it must not be recalculated from redacted replacement values.

## Transition Contract

| Stage | Preconditions and Human Review | Sign-off Effect |
| --- | --- | --- |
| Import and Anonymization | Reserve source, check for content duplicates, extract metadata, publish anonymized artifact completely and with integrity verified | Available for review; no export sign-off yet |
| Identity Verification | Cross-check first name, last name, and date of birth against the source; successfully save corrections; verify patient and examination assignment | Confirm only the verified identity revision |
| Anonymization Verification | Inspect actually published image or document, including remaining identifiers; successful text extraction alone is not sufficient | Allow annotation on the released anonymized artifact |
| Report | Annotate/materialize anonymized report domain-specifically; resolve examination and document type | Release domain data point; source file deletion is a separate transition |
| Video | Verify and finalize segments; redact areas outside released segments; verify result and unchanged clinical timeline | Atomically publish new canonical generation; tie export approval to this generation |
| Cleanup | Check references, active media leases, replacement artifact, integrity, and required approvals | Remove strictly the explicitly designated old artifact role |
| Export | Re-verify current identity, anonymization, and domain approvals as well as artifact identity | Output data point with patient identity, original content hash, and separate export artifact identity |

`FAILED` or `LOST`, missing identity, missing verification evidence, and
conflicting revisions block dependent transitions. Retries must
not generate additional publications or conflicting patient linkings. A
successful verification must not be transferred to a different revision by delayed
workers. Database modification and file deletion do not form a shared transaction:
deletion requires a post-commit executable, idempotent, and reference-checking service.

For videos, the deletion role is established: The verified redacted master
remains available for streaming and export; strictly the superseded generation
may be deleted after successful publication and passing the existing
cleanup gates. For reports, it remains to be clarified whether
"original anonymized file" designates the identifying source or also the
anonymized document. Until then, the existing contract that retains the
anonymized document remains in place.

## Failure Matrix

| Failure | Impact | Required Guard and Reproducible Test |
| --- | --- | --- |
| Extraction confuses name or date of birth | Cases are assigned to the wrong patients | Identity correction before release; test incorrectly and subsequently correctly assigned source |
| Metadata correction throws an error, existing metadata is used as success | Old identity is released, raw data could be purged | Propagate error, no alternative write path; test exception and missing result |
| Identity verification is equated with image anonymization | Visible identifiers leave the system | Separate proof of verification; correct metadata on an unredacted artifact must block |
| Metadata, artifact, or segments change after review | Sign-off applies to an unverified state | Invalidate dependent evidence; reject delayed review of an old revision |
| New report for the same patient is treated as a duplicate | Clinical information is lost | Separate content identity from patient identity; test same person with two sources and identical re-import |
| Salt/center change or identifier deletion changes the join key | Cases are split or incorrectly merged | Preserve existing identity and versioned migration contract; compare export before/after rotation and deletion |
| Wrong file or only valid copy is deleted | Annotation, re-verification, and streaming become impossible | Explicit artifact role instead of `active_file`; test abort, missing replacement, and active lease |
| Segment change competes with redaction or export | Export contains wrong areas or outdated annotation | Shared locking/revision ordering and publication protection; test competing modification and delayed worker |
| Export path checks fewer gates than another | Incompletely validated data point is exported | Shared domain preconditions for API, worker, downloads, and CLI; negative contract matrix per entry point |
| Export contains only artifact hash or local database ID | Duplicate detection and patient linking fail at the receiver | Mandatory source hash and confirmed patient identity; test round-trip export/import |
| Repeated or canceled export is treated as a new delivery | Duplicate data points or falsely reported success | Stable data point/revision identity and receipt confirmation; test abort before/after commit and retry |

## State Responsibility and Code Evidence

* `models/state/sensitive_meta.py`: `names_verified`, `dob_verified`, and
`is_verified` represent the identity verification.
* `models/state/anonymization.py`: shared status representation based on the
existing Rust rules; status display is not full proof of sign-off.
* `models/state/video.py` and `raw_pdf.py`: persisted media states.
Video export already has reviewer, timestamp, and artifact hash.
* `models/state/video_segment_validation.py`: separate segment states and
invalidation of export release. This separation must be preserved.
* `services/video_files/validation.py`: Metadata correction must successfully
yield a result before anonymization approval and cleanup begin.
This alone proves neither full identity nor image anonymization.
* `services/raw_pdf_files/validation.py`: Artifact verification, metadata correction,
release, and post-commit raw file deletion.
* `services/media/export_ready.py`: locked export promotion with center check,
segment check, artifact hash, and audit entry.
* `export/frames/export_frames_with_labels.py`: `assert_video_media_export_ready`
enforces additional export gates currently depending on the deployment profile.
The frame line contains `raw_video_hash` and `artifact_sha256`, but no
patient identity. A complete exchange contract must also consider the
versioned shared contracts in `lx_dtypes`.

Centralization should use these existing states and their transition invariants
rather than introducing another independently describable global status.
Pure state checks and database constraints belong in `models/state`;
revision binding, locks, hash checks, auditing, publication, and deletion
remain tasks for services. Safe unification requires first a negative
contract matrix of all callers; a renamed status field alone improves neither
concurrency nor sign-off safety.