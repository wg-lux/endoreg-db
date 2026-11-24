# Data preservation and video states on gc-02 and gc-10

Historical operations report of 2026-09-23, translated from German; no claim about current service state. Current approvals and readiness evidence live exclusively in the feature tracker.

| Field | Value |
| --- | --- |
| `date` | 2026-09-23 |
| `language` | en |
| `purpose` | Operations report of the explicitly commissioned preservation of existing data. Binding functionality and approval status live exclusively in the feature tracker. |


## References

| Field | Value |
| --- | --- |
| `feature` | feature-tracking/ImportPipelineRobustness.yml |
| `criterion` | import_reconciliation_and_recovery |
| `storage_rules` | docs/video_storage_normalization.md |
| `storage_approvals` | feature-tracking/VideoStorageNormalization.yml |

## Scope

| Field | Value |
| --- | --- |
| `gc_02` | 7 |
| `gc_10` | 31 |
| `profile_of_both_hosts` | site-client |
| `access` | gc-02 through a local terminal; gc-10 through SSH, using the existing service environment on each. |
| `intervention` | Preserve existing files and clinical work, add missing verifiable checksums, restore matching streaming associations, and definitively terminate expired processing attempts. |


## Results

| Field | Value |
| --- | --- |
| `videos_with_at_least_one_existing_media_file` | 38 |
| `records_with_transactional_before_after_evidence` | 37 |
| `active_import_unchanged_during_intervention` | gc-10 Video 58 |
| `fully_verified_and_added_processed_checksums` | 25 |
| `restored_streaming_associations` | 21 |
| `expired_streaming_attempts_terminated` | 3 |
| `expired_import_attempts_terminated` | 2 |
| `incomplete_videos_with_accurate_error_status` | 5 |
| `retained_annotations_gc_02` | 376607 |
| `retained_annotations_gc_10_checked_records` | 368498 |
| `retained_segments_gc_02` | 264 |
| `retained_segments_gc_10_checked_records` | 3137 |

### Processed streams current at the time, with file and key checks

| Field | Value |
| --- | --- |
| `gc_02` | 3 |
| `gc_10` | 22 |
| `no_irreversible_media_loss_demonstrated` | true |

## Raw files after validation

**assessment:** A missing raw file alone is not data loss. According to the operator, raw files are deliberately cleaned during validation through VideoExaminationAnnotation.vue. The backend path confirms intended deletion after validation when cleanup conditions are met; the anonymized master remains. Individual log evidence was not reconstructed for every historical deletion.


### Affected videos without raw files

#### Gc 02

- 2

#### Gc 10

- 12
- 34
- 36
- 54

**correction_video_12_gc_10:** The missing_raw_warning initially set during this work was withdrawn. Only integrity fields set by this intervention were restored to their previous values; other metadata were retained. The anonymized file, 29698 annotations, and 13 segments exist. The historical failed raw stream remains in history.


### Code

- ../dev/lx-annotate/frontend/src/components/VideoExamination/VideoExaminationAnnotation.vue
- endoreg_db/services/video_files/validation.py::validate_video_metadata_annotation
- endoreg_db/services/video_files/io.py::delete_raw_file_after_validation

## Findings on missing data and causes

### 1

**issue:** Missing checksums of existing anonymized files

#### Gc 02

- 4
- 6

#### Gc 10

- 13
- 14
- 15
- 19
- 21
- 23
- 25
- 26
- 27
- 34
- 36
- 38
- 39
- 40
- 41
- 44
- 45
- 46
- 47
- 48
- 50
- 51
- 52

**evidence:** Files existed and were fully hashed as decrypted plaintext. Stored processed_video_hash was empty. The original cause of missing persistence is not established; this does not imply loss of video bytes.


**impact:** The overview could not recognize existing completed streams as the current generation because their derived generation identity differed.


**action:** Checksums added through get_file_hash. For 21 streams, actual source content, source path, and existing provenance agreed; only the generation association was corrected. No files were re-encoded.


### 2

**issue:** Missing sensitive metadata

#### Gc 02

- 3
- 15

#### Gc 10

- 35
- 37
- 42
- 59

**assessment:** A missing association was found. Whether data previously existed or extraction never completed is not established for every case. No metadata were invented and no existing annotations or clinical approvals were reset. A traceable integrity warning remains for gc-02 video 3.


### 3

**issue:** Missing anonymized output

#### Gc 02

- 15

#### Gc 10

- 35
- 37
- 42
- 58
- 59

**assessment:** Raw files exist. There is no evidence that previously successful anonymized output was deleted. Video 58 had an active import during the intervention and was left unchanged. The other five videos were marked as incomplete processing with retained sources.


### 4

**issue:** Orphaned running attempts

#### Gc 02

##### Streaming artifacts

- 60
- 76
- 69

**import_video:** 15

#### Gc 10

| Field | Value |
| --- | --- |
| `import_video` | 59 |
| `evidence` | Expired processing leases or stale materialization attempts. The gc-02 import was approximately eight days old. The streaming follow-up start condition considers open statuses without excluding expired import leases, which could block it. |


**action:** Import ownership fenced again through the existing lease service and failure finalized. Stale streaming attempts terminated under row locks without cleaning up keys, paths, segments, or source files.


### 5

**issue:** Missing historical streaming provenance

#### Gc 02

##### Processed videos

- 4
- 6

##### Raw videos

- 3
- 4
- 5
- 6
- 13

**assessment:** Old artifacts without an evidenced source content hash. Safe automatic adoption based only on filenames or file existence is impossible. Files and keys remain; no master loss was demonstrated.


### 6

**issue:** Streaming belongs to another generation

#### Gc 02

- 5

#### Gc 10

- 53
- 54

**assessment:** Existing older streams do not match the current processed source. No unsupported reassignment or deletion was performed. Current masters and annotations remain.


### 7

**issue:** Regeneration on gc-10 fails because of the graphics driver environment

#### Particularly visible for processed streams

- 44
- 50

**evidence:** Loaded NVIDIA kernel module 580.119.02 and NVIDIA Management Library (NVML) 595.45 do not match. Workers report unsupported device. This establishes a processing blocker at the time, not the cause of every historical import failure. On gc-02, versions 595.45.04 were consistent.


### 8

| Field | Value |
| --- | --- |
| `issue` | Low free storage |
| `gc_02` | Approximately 94 percent used, 32 GiB free at diagnosis. |
| `gc_10` | Approximately 95 percent used, 51 GiB free at diagnosis. |
| `assessment` | Residual operational risk for new processing. GiB means gibibytes. No media files deleted to reclaim space; existing encrypted storage continued in use. |


### 9

| Field | Value |
| --- | --- |
| `issue` | Generic error path can remove streaming keys and paths |
| `code` | endoreg_db/services/streaming/hls_media.py::_mark_artifact_failed |
| `assessment` | Cleanup behavior observed in code; no proof that this path caused a specific historical loss. It was explicitly not used for the three stale streaming attempts during this intervention. |


## Protection and checks

- Before every change, encrypted evidence stored and read back in the respective local media storage.
- Planned changes first executed inside transactions and rolled back.
- Changes under database row locks with active media lease checks.
- 25 complete plaintext checksum checks through the existing hash helper.
- Existing canonical files additionally checked through authenticated reads of beginning and end ranges.
- Annotations and segments compared through full row-data checksums before/after intervention.
- Frame count, frame rate, duration, time coordinates, and clinical approval fields unchanged.
- Master references, filesystem identity, file size, and modification time unchanged before/after intervention.
- Streaming keys, initialization vectors, paths, segment count, and source content hashes unchanged.
- Final verification: all completed streams selected by the overview have existing files and decryptable keys.
- No raw media export; no patient data, credentials, or keys in this document.

Verification scope and outstanding evidence for the historical intervention are in
[ImportPipelineRobustness.yml](../feature-tracking/ImportPipelineRobustness.yml),
criterion `import_reconciliation_and_recovery`.

## Derived backend changes

**request:** Review repair findings for necessary changes in endoreg_db.

### Raw file reconciliation

| Field | Value |
| --- | --- |
| `file` | endoreg_db/services/runtime/reconciliation.py |
| `finding` | Startup reconciliation could reassociate an old input copy after clinical validation when a raw file reference was still set. |


**change:** Validated videos are excluded before candidate search. Before file promotion, the video is reloaded under a database row lock. Intervening validation, changed source identity or reference, or a raw file that has reappeared prevent the change.


### Overview

| Field | Value |
| --- | --- |
| `file` | endoreg_db/serializers/misc/file_overview.py |
| `finding` | Historical failed raw streams could be displayed as current errors even after successful clinical validation. |


**change:** Only the historical failed raw stream is omitted from the current overview when clinical validation exists and the completed anonymized stream matches the current source metadata identity. Active raw processing and missing, stale, or failed anonymized streams remain visible. Stored history is retained.


### Other reviewed findings

- Current import and anonymization paths already save processed_video_hash before ensure_video_hls. No evidenced new cause of historical checksum gaps was found in these paths; no speculative automatic adoption of old streaming generations was added.

- General video integrity checking checks the canonical anonymized master and does not mark a video as lost solely for a missing raw file.

- Startup reconciliation already deliberately retains stale import states because association with the owning processing attempt is missing. No blanket reset was added that could endanger active processing.

- The NVIDIA driver conflict and streaming follow-up start condition belong to host/service configuration; this backend change does not fix them.


**delivery:** Source change in the working directory; not yet deployed to gc-02/gc-10.

### Code checks

- Focused Pyright for the four changed Python files: 0 errors, 0 warnings. Ruff check and format --check passed.

- Combined pytest run for test_reconciliation_service.py, test_anonymization_overview.py, and test_media_integrity_reconciliation_plan.py: 76 passed; three new cases initially failed because the fixture lacked the required center field. After correction and another type check, these three cases passed in a focused rerun. In total, 79 distinct cases verified successfully; existing plugin/dependency warnings.

- Database cases simulate a change during candidate search. SQLite checks the repeated state comparison, not the effectiveness of concurrent PostgreSQL row locks.

- Full project Pyright initially reported six errors and six warnings. Five errors in new test functions were corrected and checked with focused verification. The remaining error observed outside this scope concerned serializers/report/patient_examination_report.py:399 (attached_findings, partially unknown list type). No error-free full check is claimed.


## Evidence

| Field | Value |
| --- | --- |
| `base` | Per host get_runtime_paths().storage; encrypted storage through the existing Django media storage. |
| `gc_02` | recovery/video-preservation-20260923-89225c4a1ea342d68d2938f1b7f03134 |
| `gc_10` | recovery/video-preservation-20260923-60aea4d60b024df495230b8b88c4f073 |

### Files

- before.json
- verified-processed-identities.json
- rollback-test-summary.json
- rollback-test-video-<id>-before.json / -after.json
- executed-preservation-command.py
- committed-video-<id>-before.json / -after.json
- committed-summary.json
- final-verification.json
- gc-10 additionally: raw-cleanup-correction-before.json / raw-cleanup-correction-after.json

**note:** Original unchanged execution evidence still contains the initially set raw-file warning for video 12. The separate correction evidence and final verification show its withdrawal. Evidence remains encrypted locally.


## Individual evidence

### Gc-02

| video | raw_file | anonymized_file | annotations | segments | actions |
| --- | --- | --- | --- | --- | --- |
| 2 | false | true | 35786 | 36 | Existing data retained unchanged |
| 3 | true | true | 67115 | 40 | Stale streaming attempt terminated, artifact 60; Warning for missing sensitive metadata |
| 4 | true | true | 90039 | 69 | File checksum added |
| 5 | true | true | 175670 | 83 | Stale streaming attempt terminated, artifact 76 |
| 6 | true | true | 3428 | 1 | File checksum added; Stale streaming attempt terminated, artifact 69 |
| 13 | true | true | 4569 | 35 | Existing data retained unchanged |
| 15 | true | false | 0 | 0 | Expired import attempt fenced and terminated; Processing incomplete; Source retained |

### Gc-10

| video | raw_file | anonymized_file | annotations | segments | actions |
| --- | --- | --- | --- | --- | --- |
| 12 | false | true | 29698 | 13 | Raw-file warning withdrawn; expected cleanup |
| 13 | true | true | 7133 | 14 | File checksum added; Streaming association corrected, artifact 2 |
| 14 | true | true | 18804 | 19 | File checksum added; Streaming association corrected, artifact 3 |
| 15 | true | true | 13142 | 40 | File checksum added; Streaming association corrected, artifact 4 |
| 19 | true | true | 9566 | 23 | File checksum added; Streaming association corrected, artifact 5 |
| 21 | true | true | 21351 | 61 | File checksum added; Streaming association corrected, artifact 6 |
| 23 | true | true | 4197 | 15 | File checksum added; Streaming association corrected, artifact 8 |
| 25 | true | true | 1489 | 5 | File checksum added; Streaming association corrected, artifact 9 |
| 26 | true | true | 61817 | 43 | File checksum added; Streaming association corrected, artifact 10 |
| 27 | true | true | 26715 | 51 | File checksum added; Streaming association corrected, artifact 11 |
| 34 | false | true | 10321 | 51 | File checksum added; Streaming association corrected, artifact 12 |
| 35 | true | false | 0 | 0 | Processing incomplete; Source retained |
| 36 | false | true | 48856 | 38 | File checksum added; Streaming association corrected, artifact 13 |
| 37 | true | false | 0 | 0 | Processing incomplete; Source retained |
| 38 | true | true | 0 | 21 | File checksum added; Streaming association corrected, artifact 80 |
| 39 | true | true | 0 | 0 | File checksum added; Streaming association corrected, artifact 83 |
| 40 | true | true | 0 | 0 | File checksum added; Streaming association corrected, artifact 58 |
| 41 | true | true | 0 | 0 | File checksum added; Streaming association corrected, artifact 59 |
| 42 | true | false | 0 | 0 | Processing incomplete; Source retained |
| 44 | true | true | 0 | 0 | File checksum added |
| 45 | true | true | 0 | 0 | File checksum added; Streaming association corrected, artifact 68 |
| 46 | true | true | 0 | 0 | File checksum added; Streaming association corrected, artifact 69 |
| 47 | true | true | 0 | 0 | File checksum added; Streaming association corrected, artifact 70 |
| 48 | true | true | 0 | 0 | File checksum added; Streaming association corrected, artifact 71 |
| 50 | true | true | 0 | 0 | File checksum added |
| 51 | true | true | 0 | 470 | File checksum added; Streaming association corrected, artifact 73 |
| 52 | true | true | 0 | 504 | File checksum added; Streaming association corrected, artifact 74 |
| 53 | true | true | 83564 | 1186 | Existing data retained unchanged |
| 54 | false | true | 31845 | 583 | Existing data retained unchanged |
| 58 | true | false | Active import; no change | Active import; no change | Active import left unchanged |
| 59 | true | false | 0 | 0 | Expired import attempt fenced and terminated; Existing technical video metadata confirmed in state; Processing incomplete; Source retained |
