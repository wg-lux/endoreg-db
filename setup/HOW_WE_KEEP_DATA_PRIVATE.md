# Sensitive metadata lifecycle for video imports

## Scope

This document describes the current repository behavior for creating and
updating `SensitiveMeta` records during video processing. It is an implementation
reference, not a claim that a particular deployment has completed its operational
privacy or security review.

## Current workflow

Video initialization does not require or create a `SensitiveMeta` record.
`VideoFile.create_from_file_initialized()` delegates to
`endoreg_db.services.video_files.create_initialized_video_file_from_path()`, and
initialization records video specifications, state, streamable-artifact state,
and frames.

Sensitive metadata is created later at the text-extraction boundary:

1. `update_video_text_metadata()` extracts or accepts a validated
   `VideoTextMetaPayload`.
2. It adds the video's center name when the payload does not contain one.
3. It splits any external patient identifier from the model payload.
4. It calls `update_or_create_sensitive_meta_from_dict()`, creating a record
   when the video has none or updating the existing record.
5. It links a newly created record to the video and marks text metadata as
   extracted and processed.

If extraction returns no payload, the workflow marks text extraction complete
but does not invent and attach a new `SensitiveMeta` record.

## Creation invariants

`SensitiveMeta.create_from_dict()` accepts either an existing `Center` object in
`center` or a center name in `center_name`. One of them is required, and an
unknown center name fails loudly.

Before the first save, the creation helper:

- normalizes the patient date of birth and examination date;
- resolves the center and patient gender;
- uses `"unknown"` for missing patient names and text;
- generates a random date of birth or examination date when either is missing;
- calculates patient and examination hashes; and
- creates or resolves the linked pseudonymous patient, examination, and
  examiner records.

These defaults describe current fallback behavior. They are not verified
patient identity and must not be presented as extracted clinical data.

## Updates and identity

`SensitiveMeta.update_from_dict()` delegates to
`update_sensitive_meta_from_dict()`. The helper normalizes supported fields,
applies them to the existing instance, updates name lookup data when necessary,
and calls `save()`.

Every ordinary `SensitiveMeta.save()` recalculates the patient and examination
hashes from the current names, date of birth, examination date, center, and the
configured salt. It then resolves the pseudonymous relations for those hashes.
Consequently, changing identity-bearing input can change both hashes and their
linked pseudonymous records.

Code that intentionally anonymizes an already validated identity uses a
separate preservation path: after applying anonymized field values it restores
the committed hashes and foreign keys directly. Do not replace that behavior
with an ordinary save, which would recalculate identity from anonymized
placeholders.

## Failure behavior

Creation or saving fails when required invariants cannot be resolved, including
a missing center. Hash calculation also
requires names, date of birth, examination date, and center after normalization.
Errors from the text-metadata transaction are logged and re-raised as processing
failures; the transaction does not silently publish a partial metadata update.

## Verification and implementation references

- Model boundary: `endoreg_db/models/metadata/sensitive_meta.py`
- Creation, update, hashing, and pseudonym logic:
  `endoreg_db/models/metadata/sensitive_meta_logic.py`
- Video text-metadata workflow:
  `endoreg_db/services/video_files/_metadata/text_meta.py`
- Video initialization workflow: `endoreg_db/services/video_files/imports.py`
- Metadata update tests: `tests/services/test_sensitive_meta_update.py`
- Optical character recognition boundary tests:
  `tests/services/test_video_metadata_ocr_boundary.py`
