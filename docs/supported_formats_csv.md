# Supported Tabular Import Formats

This repository now treats supported CSV/TSV table shapes as YAML-backed templates.

Canonical template registry:
- [document_templates.yaml](../endoreg_db/data/tabular_import_format/document_templates.yaml)

Resolver and normalizer:
- [tabular_import_formats.py](../endoreg_db/services/tabular_import_formats.py)

The import flow is:
1. Read the header columns.
2. Resolve the best matching stored template.
3. Normalize each row into canonical snake_case fields.
4. Derive a best-effort pre-anonymized ingest payload for `endoreg_db`.

This is template-driven, not auto-learning. Unknown headers are preserved for review, but new production formats should be added to the YAML registry explicitly.

## Supported Formats

`cwd`
- `PatientNr`
- `FallNr`
- `Dokumentzeit`
- `Dokumentnummer`
- `Dokumentversion`
- `pmdAnam`

`bewegungen`
- `PatientNr`
- `FallNr`
- `Zugangszeit`
- `Behandlungsort`
- `Fachabteilung`
- `Zimmer`

`briefe`
- `PatientNr`
- `FallNr`
- `dateErstellzeit`
- `strText`

`diagnosen`
- `PatientNr`
- `FallNr`
- `Diagnoseschluessel1`
- `Diagnosezeit`
- `KzAufnahmediagnose`
- `KzBehandlungsdiagnose`
- `KzEntlassdiagnose`
- `KzFachabteilungshauptdiagnose`
- `KzKrankenhaushauptdiagnose`
- `KzNebendiagnose`
- `KzOperationsdiagnose`

`labor`
- `PatientNr`
- `FallNr`
- `Dokumentzeit`
- `Leistung`
- `Leistungstext`
- `Messwert`

`meona_medikamente`
- `PatientNr`
- `id`
- `tradename`
- `patient_id`
- `apply_date`
- `prepare_date`
- `creation_date`
- `actual_dose`
- `unit_dose_name`
- `main_application_id`
- `main_order_id`
- `status`

`pathodocs`
- `PatientNr`
- `FallNr`
- `Dokumentnummer`
- `Dokumentzeit`
- `DokumenttypID`

`patienten`
- `PatientNr`
- `PatientAlter`
- `Geschlecht`

`prozeduren`
- `PatientNr`
- `FallNr`
- `OPCode`
- `Beginnzeit`

`radiologie`
- `PatientNr`
- `FallNr`
- `Dokumentzeit`
- `Dokumentnummer`
- `Dokumentversion`
- `kurbefund`
- `befund`
- `beurteilung`

`stammdaten`
- `PatientNr`
- `FallNr`
- `PatientAlter`
- `Geschlecht`

## Current Database Mapping Boundary

The current best-effort pre-anonymized ingest payload primarily derives:
- `external_id` from `patient_nr`
- `external_id_origin` from the source system
- `casenumber` from `fall_nr`
- `examination_date` and `examination_time` from the first matching datetime field
- `anonymized_text` from one of:
  - `pmd_anam`
  - `str_text`
  - `befund`
  - `kurbefund`
  - `beurteilung`

All normalized source columns remain available in the canonical row and raw column map for later database-specific mapping.

## SAP IS-H Zip Workflow

For SAP IS-H exports delivered as `.zip` bundles containing tab-separated `.txt`
tables, use the management command:

```bash
python manage.py import_sap_ish_zip /path/to/sap_export.zip
```

What it does:
- extracts the zip archive
- reads each `.txt` as TSV
- matches supported table headers against the YAML template registry
- joins rows primarily by `PatientNr` and `FallNr`
- prefers text-bearing rows like `briefe`, `cwd`, and `radiologie`
- emits watcher-ready preanonymized `.txt` plus `.json` sidecar pairs

Useful options:
- `--output_dir /path/to/output`
- `--source_system sap_ish`
- `--center_name my_center`
- `--center_key my_center_key`
- `--process`

`--process` immediately runs the generated files through
`process_preanonymized_watcher_file(...)`. Without `--process`, the command only
prepares the watcher-compatible drop files.
