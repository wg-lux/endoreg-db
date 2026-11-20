# 1.8.8 — Unreleased

- Requires `lx-dtypes>=0.7.1` for package kinds and deployment setup schema 2.0.
  Publish the matching core package before resolving or deploying this release.
- Migrate schema 1.0 setup files with `setup_deployment migrate <file> --output
  <new-file>`, then generate and review a new plan and lock before applying it.
- Adds electrosurgical measurement import and video alignment (migration 0119),
  and German reference-catalog display names (migration 0120).
- Review migrations through `0120_reference_catalog_name_de` before deployment.
  Release-preparation evidence is recorded in
  `feature-tracking/reports/release_preparation.yml`.

# Unreleased — 2026-11-02: LXENC v2 and migration safety

These are working-tree changes, not a published version or deployment approval.
Choose your starting point in the [documentation guide](docs/README.md#upgrading-or-recovering-encrypted-media);
operators can use the [migration result guide](docs/secret_rotation.md#read-the-media-migration-result)
to decide what to do after a command finishes.
LXENC v1/v2 names the encrypted **file format**, not the application version or
LUKS disk format. Both file formats retain the `LXENC01` magic prefix.

- New files use v2: authenticated end records, key selection by `key_id`, and a
  wrapped data key bound to `file_id`. Existing v1 files remain readable.
- Python and native byte-range readers reject damaged end records even for early
  ranges. Storage responses keep one file generation across atomic replacement.
- Explicit media rotation upgrades v1 files even when their active key is
  unchanged. Repeating it authenticates and skips already-current v2 files.
- The legacy six-field native header API is preserved. Python and native readers
  share chunk rules; incompatible native artifacts fail before new writes.
  Backward compatibility covers v1 data reads, not reading v2 with old software.

**On v1 machines:** keep existing keys and a tested encrypted backup. Deploy
matching v2-capable Python/native readers to every consumer before allowing new
writes. Configure the private media keyring, preview, then explicitly apply the
[v1 upgrade procedure](docs/secret_rotation.md#v1-machines-and-interrupted-media-migrations).

**Mid-migration:** retain active and retiring keys, published files, and staging
evidence. Finish or stop the existing rotation job cleanly; resume with the same
intended keyring after upgrading readers. Resolve reported failures, deferred
leases, missing/unmanaged files and staging leftovers before rerunning. Do not
restart from empty storage, generate replacement keys, or downgrade to v1-only
readers. Use the same [resume procedure](docs/secret_rotation.md#v1-machines-and-interrupted-media-migrations).

**Before retiring keys:** verify old/new reads and backups, replicas and HLS.
V1 needs an independent reference against historical whole-chunk loss; `file_id`
does not yet bind files to database records. See the
[review and verification](feature-tracking/reports/lxenc_v2_review.md).

# Boundaries were enforced to reduce errors from mixed responsibilities

Persistence, service, and data-model layers were separated where practical. This reduces circular dependencies between models and services. Validation boundaries were strengthened during this work.

Hub transfer is production-critical. Remaining readiness work includes deployed envelope-encryption and key-rotation verification, plus production-like recovery, cleanup, and capacity exercises. The authoritative status is maintained in `feature-tracking/HubTransfer.yml`.

Report resolution now supports various input knowledge bases.

# API view and local-production improvements

Continuous integration currently runs Ruff, mypy, repository smoke checks, and the fast pytest lane. Local pre-commit checks also run Pyright and repository contract checks.


# Model relationship reorganization

## Examination
- Many to Many relationship between Examination and ExaminationIndication is now defined in the Examination Model
- Many to Many relationship between Examination and ExaminationTime has been added to the Examination Model
- Many to Many relationship between Examination and Finding has been added to the Examination Model


## Finding
- Many to Many relationship between Finding and FindingClassification is now defined in the Finding Model
- Many to Many relationship between Finding and Examination is now defined in the Examination Model

## FindingClassification
- Removed the Many to Many relationship between FindingClassification and Examination
    - replaced by property which uses findings to retrieve related examinations

## Dataloader
- changed load order of base db data to accommodate new relationships
    - ExaminationIndication data is now loaded before Examination data
    - Finding data is now loaded before Examination data and before ExaminationIndication (depends on FindingInterventions) data

Current implementation and production-readiness work is tracked only in `feature-tracking/*.yml`; this changelog does not maintain a parallel to-do list.
