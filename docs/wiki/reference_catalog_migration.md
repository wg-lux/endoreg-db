# Current reference catalogue reconciliation

Owner: endoreg-db maintainers. Scope and readiness are recorded in
[DtypesStudyDefinitions.yml](../../feature-tracking/DtypesStudyDefinitions.yml).

## Command sources

`load_base_db_data`, `load_base_db_data --reconcile-legacy`, and
`migrate_reference_catalog` now reconcile `endoreg_reference@2.0.0` through the
same typed importer. The older command names remain usable, but no longer invoke
the historical colorectal relationship repair. The compatibility flag does not
permit overwriting clinical definitions.

The application catalogue references terminology-owned units and information
sources. Their pinned graph and the application rows commit in one transaction.
Examinations, indications and findings are provisioned separately from the
selected terminology. Compatibility clinical loaders require an explicit registered module and version.
Users select or upload terminology in the reporting or study setup workflow before
loading examinations and findings. There is no default clinical package.

## Preview and apply

Provision the shipped terminology registry before a dry run. A dry run never
hydrates registry files or writes database rows.

```sh
python manage.py load_base_db_data --dry-run --format yml
python manage.py load_base_db_data
python manage.py migrate_reference_catalog --dry-run --format yml
```

Both entry points adopt equivalent existing rows, preserve primary keys, validate
all dependency definitions, and reject conflicting meanings without partial
writes. Repeating a successful import is idempotent. Dependency-backed catalogues
always reconcile clinical and application rows; they do not use the historical
receipt-only shortcut. No command activates a study or imports patient data.

For clinical definitions, review the selected package explicitly:

```sh
python manage.py import_clinical_reference_data --module coloreg --module-version 0.3.0 --dry-run --adopt-existing
```

Preserve conflict output and resolve differing clinical meanings through a
reviewed terminology release or a separately reviewed migration. There is no
force-overwrite option. Local test results do not authorize production changes.

## Historical evidence

The immutable `colorectal_indication_interventions_v1` specification and its
`endoreg_reference@1.0.0` digest remain historical evidence for existing receipts
and service-level regression tests. They are not the source of current CLI
provisioning. Do not rewrite their digest or interpret old approval evidence as
approval for a different catalogue.
