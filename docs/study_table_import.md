# Importing Study Tables with YAML Mappings

Responsible: endoreg_db, lx_dtypes, and lx-annotate. Scope and audit trails are exclusively located in [DtypesStudyDefinitions.yml](../feature-tracking/DtypesStudyDefinitions.yml), criteria `researcher_table_import`, `dataset_lifecycle`, and `study_persistence`.

## Persisted Studies

`endoreg_db.models.other.study.Study` stores studies independently of lx-annotate: UUID, name, hypothesis, description, owner, and timestamp. The shared contract `lx_dtypes.models.contracts.study.StudyDefinition` validates the fields at the API and model boundaries.

`GET/POST media/studies/` and `GET/PUT/DELETE media/studies/{id}/` manage one's own studies. POST/PUT accept `name`, `hypothesis`, and `description`; owner and UUID are assigned server-side. Reading requires `patient:read`, writing requires `patient:write`. Foreign studies are not accessible.

The `StudyCohort` model is also located in **endoreg-db**, under `endoreg_db/models/other/study_cohort.py`. Its foreign key `study` allows multiple cohorts per study. For cohort POST/PUT, `study_id` selects an owned study; the response contains the same ID. Without an ID, a new cohort automatically receives its own study. PUT without `study_id` keeps the previous study. Historical standalone cohorts or those created directly via the ORM may initially lack a study reference; a study will be created upon the next API save.

Deleting a study removes its cohort definitions and their links. Datasets, patients, clinical datasets, and media are retained. Deleting a single cohort does not remove its study. Study-wide datasets result from the cohort links; there is no duplicate membership table in the study model.

EndoReg migrations `0095_study` and `0096_studycohort` create the tables without depending on lx-annotate. lx-annotate migration `0014_studycohort_study` first creates parent studies; `0015_move_studycohort_to_endoreg` moves cohorts and both membership tables into endoreg-db. IDs, UUIDs, owners, timestamps, and JSON definitions are preserved. Primary key sequences are updated. An already populated target table blocks the migration instead of overwriting existing data. The rollback path restores the previous cohort tables and moves their contents back.

Existing Python imports from `lx_annotate.models.study_cohort` and `lx_annotate.models` remain intact as re-exports. The registered model name is now `endoreg_db.StudyCohort`; Django model permissions therefore use the app label `endoreg_db`. API workflows remain in lx-annotate. Both applications and the shared contract must be deployed together.

## Workflow for Researchers

1. Save and select a cohort with a name and hypothesis in lx-annotate.
2. Select **New mapping from template** or copy the [example mapping](../endoreg_db/data/study_import/colonoscopy.yml). Adapt column names to your own table, assign a custom UUID as the `mapping_id`, and select an existing knowledge base version. The template in the UI automatically receives a new UUID.
3. In the **Import study table** section, load or edit the YAML file and select **Save mapping**. Already saved mappings are available in the dropdown list.
4. Select the authorized center and the CSV, XLSX, or XLS file.
5. **Validate table** executes the clinical import, dataset attachment, and study ledger capture, including database constraints and evaluation against the exact persisted full package snapshot; the complete transaction is rolled back.
6. **Import into cohort** validates again and atomically saves clinical rows, the linked dataset, and immutable study collection entries for ledger Excel export or hub transfer. A missing package snapshot, invalid observation, or capture failure rolls back the clinical import, dataset attachment, and collection entries. Previously saved mapping files remain available. The preview is subsequently reloaded.

One dataset is created per file import. The same user, mapping ID, center, and file content will reuse the same dataset upon repetition. The name comes from the mapping; the API allows `dataset_name`. Clinical datasets have `dataset_type: clinical` and `ai_model_type: null`. They can therefore be managed independently, even without videos or annotations.

## Managing Datasets and Studies

The study page and dataset settings offer **Manage datasets**:

* Creation, viewing, and renaming, including description.
* **Merge** creates a set-theoretically distinct union of selected datasets. Source mappings and clinical objects remain intact. If modalities differ, a clinical research dataset is created.
* **Split** creates two independent datasets from selected and remaining patients. All examinations, videos, and annotations for a patient are kept together. The API allows up to 20 disjoint, complete patient groups. A video without a distinct patient blocks the split.
* Study assignments can be added and removed. Studies themselves can be read, edited, and deleted; deleting them does not remove any datasets.

Datasets are modified by the owner or a superuser. Full center authorization is additionally checked. Historical datasets without an owner can be managed by superusers. The system default dataset is protected against modification and deletion. An active training or export run blocks deletion.

**Delete dataset** first displays the affected mappings and requires an explicit selection:

1. Remove only the dataset, memberships, and study links.
2. Additionally remove verifiably exclusively imported examinations along with their unaltered findings.

The preview lists the removable examinations as well as reasons for retained datasets. Shared, media-linked, subsequently edited, and historical examinations without proof of ownership are kept. Patient identities, media files, and annotations are never deleted by this action. Direct video-to-dataset assignments switch to the existing system default. Import receipts remain with an empty examination link; a new mapping ID is required to re-import explicitly deleted rows.

The server requires the signed preview token, no older than 15 minutes, along with the `mode`. It re-checks current membership, links, clinical contents, and permissions within the transaction. An outdated preview must be reloaded. Existing training split snapshots are retained even after their source dataset is deleted.

| Route under `media/studies/` | Methods and Payload |
| --- | --- |
| `datasets/` | GET list; POST `name`, optional `description`, `dataset_type`, `ai_model_type`, `is_active` |
| `datasets/{id}/` | GET details; `?members=true` appends patient groups; PUT `name`, `description`, `is_active`; DELETE `mode`, `preview` |
| `datasets/combine/` | POST `name`, `sources: [id, …]` |
| `datasets/{id}/split/` | POST `partitions: [{name, patients: [id, …]}, …]` |
| `datasets/{id}/deletion-preview/` | GET signed token, `removable`, `retained`, and affected memberships |
| `cohorts/{id}/` | GET, PUT, DELETE of owned study |

Deletion modes are named `memberships_only` and `exclusive_imported_records`. The pre-existing route `settings/application/ai_datasets/{id}/study_cohorts/` reads study links and sets them using POST `cohort_id`, `linked`.

A table row describes one examination. `row_id` must permanently identify this source row, even after re-sorting or re-exporting. Multiple findings from the same examination are mapped as named column groups. Multiple rows may supplement the same examination but then require different, stable source identities.

## Research spreadsheets and LX exchange workbooks

Use a YAML mapping for existing researcher CSV/XLS/XLSX files: it maps their
wording into an exact versioned KB and creates validated clinical observations.
The study ledger download is a separate lossless exchange workbook whose canonical
payload retains study snapshots and assessments. Import that workbook through the
ledger exchange action, not through an inferred column mapping. Human-readable
worksheets support review; editing their display cells is not a clinical correction
workflow. Hub transfer and ledger workbook exchange use the same shared exchange
contract.

## Mapping Format 1.0

The shared contract is `lx_dtypes.models.contracts.table_import.TableMapping`.
The parser loads its exact registered package through `lx_dtypes.packages.load_registered_package`
and validates ledger observations against that package before persistence.
`TableMapping.model_json_schema()` provides the machine-readable schema.
Unknown target fields, duplicate YAML keys, YAML aliases, and executable tags are rejected. A mapping cannot load Python modules or define permissions.

| Key | Meaning |
| --- | --- |
| `mapping_id`, `name`, `schema_version` | Stable mapping identity, display name, and format version |
| `knowledge_base` | Exact `knowledge_base_module` and `knowledge_base_version` |
| `source` | `delimiter`, `encoding`, `sheet`, and 1-based `header_row` |
| `row_id` | Permanent source identity per examination row |
| `first_name`, `last_name`, `dob`, `gender` | Complete patient identity and explicit gender terminology |
| `examination_date`, `examination` | Examination date and canonical examination concept name |
| `finding` | Optional short form for a finding group |
| `findings` | Named finding groups with `finding` and `classifications` |

A string is an exact column name; a list concatenates multiple columns in the specified order. Physical proximity is not guessed. The full form is:

```yaml
first_name:
  columns: [Patient.Vorname]
  required: true

```

`columns` and `value` are mutually exclusive. `value` sets an explicit constant value, for example, `examination: {value: colonoscopy}`. `separator` determines the concatenation of multiple columns; the default is a line break. `values` is a complete mapping of source values to canonical values. An unknown, non-empty value is rejected; there is no fuzzy automatic concept selection.

`date_format` uses the Python `strptime` notation, for example, `'%d.%m.%Y'`. Native Excel date cells are read as ISO calendar dates. Without a date format, ISO dates (`YYYY-MM-DD`) are required. `required: false` allows empty optional finding fields; it is not permitted for patient and examination identities. Missing source columns remain an error even for optional values.

Named groups receive stable finding identities independently of one another:

```yaml
findings:
  polyp_1:
    finding:
      columns: [Polyp_1_Befund]
      values: {Polyp: colon_polyp}
    classifications:
      lesion_planarity_default:
        columns: [Polyp_1_Planaritaet]
        values: {flach: lesion_planarity_flat}

```

Format 1.0 also accepts an expanded classification with a `choice` column and typed
`descriptors`. Existing column shorthand is unchanged. For example, a researcher
can retain their own lesion identifier column while targeting a ColoReg package:

```yaml
findings:
  index_lesion:
    finding: {value: coloreg_polyp_resection}
    classifications:
      coloreg_lesion_id:
        choice: {value: coloreg_lesion_id_documented}
        descriptors:
          coloreg_lesion_id_value:
            column: Meine_Laesionskennung
            value_type: text
      lesion_size_mm:
        choice: {value: lesion_size_oval_mm}
        descriptors:
          lesion_size_mm_value:
            column: {columns: [Durchmesser_mm], values: {'klein': '5', 'mittel': '15'}}
            value_type: number
```

Use an exact ColoReg knowledge-base version that defines these concepts (for
example `coloreg` / `0.3.0`). Source wording is local to the mapping; it never
changes canonical concept names. Numeric aliases such as the example above must
reflect the researcher's documented source convention, not inferred clinical
measurements. A `number` uses a decimal point and must be finite; `integer`
requires integral decimal spelling. `boolean` accepts exactly `true` or `false`
after explicit `values` aliases, for example `values: {ja: 'true', nein: 'false'}`
(quote YAML boolean outputs); `selection` accepts a JSON array of strings.
The KB remains authoritative for descriptor type, numeric bounds, text length,
selection options/cardinality, and choice membership. Empty optional cells omit
an observation; they do not manufacture a zero or false. A descriptor without a
finding or selected choice fails validation. Named groups and permanent row IDs
preserve lesion identity when columns or rows are reordered.

Interventions and laboratory aggregates are not permitted target fields in this version. They will be rejected and never silently discarded. New clinical concepts must be defined in the versioned terminology package and projected into existing database references; the compatibility route for this is `load_base_db_data`. The table import does not create terminology.

## spaCy and Custom Extractors

Individual mappings can invoke a registered extractor. The built-in `spacy_patient` uses `lx_anonymizer.ner.spacy_extractor.PatientDataExtractor`:

```yaml
first_name:
  columns: [Patientenzeile]
  extractor: spacy_patient
  output: first_name
last_name:
  columns: [Patientenzeile]
  extractor: spacy_patient
  output: last_name
dob:
  columns: [Patientenzeile]
  extractor: spacy_patient
  output: dob

```

A fresh extractor instance is used for each new text input. Results from the same input are reused only within the same table row. An unrecognized name or `unknown` does not fulfill the identity check. Pydantic and the Knowledge Base validate the extracted results prior to clinical import. spaCy itself does not confirm clinical correctness. The existing lx-anonymizer model must be available locally.

Trusted Python callers can pass `extractors={"custom_name": callable}`. A callable receives a string and returns `Mapping[str, object]`; it must process inputs independently. Its results are subject to the same mandatory field and terminology checks. The HTTP API exclusively provides the hardcoded, registered extractors.

```python
from pathlib import Path
from endoreg_db.services.studies.table_parser import parse_table_records
from endoreg_db.services.studies.table_import import import_table

# No database writes: validated TableRecord objects with LX snapshot.
records = parse_table_records(Path("import_spec.yml"), Path("study.csv"))

# owner and center are already authorized, saved Django objects.
result = import_table(
    Path("import_spec.yml"), Path("study.csv"),
    owner=owner, center=center, dry_run=True,
)

```

## API and Storage

The following routes are located under the existing `endoreg-api/` mount; additional proxy prefixes are handled by the central frontend accessor.

| Method and Route | Contract |
| --- | --- |
| `GET media/studies/table-mappings/` | Own mappings (`id`, `name`), permitted centers, and `template` with a new mapping ID |
| `POST media/studies/table-mappings/` | JSON `{"yaml": "…"}`; validates and saves an immutable mapping |
| `GET media/studies/table-mappings/{uuid}/` | JSON with the YAML text in the `yaml` key |
| `POST media/studies/cohorts/{id}/table-import/` | Multipart: `file`, `mapping_id`, `center_id`, `mode=validate` or `mode=import` |
| `POST media/studies/cohorts/preview/` | Existing cohort definition; optional `cohort_id` for the imported examination scope |

Authentication is required even in debug mode. Reading requires `patient:read`, writing requires `patient:write`; superusers are permitted. Mapping owner and cohort owner are derived from the logged-in user. Center rights are re-verified on every import and preview.

Mappings are located under `get_runtime_paths().table_mappings / <owner_id> / <mapping_uuid>.yml`, i.e., `<LX_RUNTIME_ROOT>/storage/study_table_mappings/…`. API callers cannot select server paths. Files are created atomically with mode `0600` and directories with `0700`. The same identity and identical content can be repeated; differing content under the same mapping ID returns a `409`.

Uploads are temporarily processed within the protected runtime storage and subsequently removed. Source file and mapping are identified via `get_file_hash`. Limits: 20 MiB upload, 10,000 data rows, 500 columns, 100,000 characters per cell, and 1 MiB YAML. Unzipped XLSX files may not exceed 100 MiB. If multiple worksheets exist, `source.sheet` is required. XLSX formulas and error cells are rejected; XLS uses stored cell values read by xlrd and does not execute macros or formulas.

Import responses contain `rows`, `created`, `replayed`, `videos`, `reports`, `captured_collections`, `dry_run`, `dataset_id`, and the saved `patient_examination_ids`. For a validation run, the ID list is empty. Row errors contain the row number, target field, and error code, not the original cell values. Database sequences can advance even during a rolled-back validation.

## Identity, Repetition, and Cohorts

The existing SensitiveMeta service maps the complete identity from first name, last name, date of birth, and center, managing salt, collision checking, pseudonymization, and examination identity. No competing hash algorithm is introduced. An incomplete name or missing date of birth blocks the import. Gender is explicitly specified; `unknown` can be an existing terminology value without replacing the complete hash identity.

Media are assigned via patient **and** examination hash within the same center. A matching patient identity alone is insufficient to assign a video to a specific examination. Existing contradictory patient or examination links block the import. Media files, anonymization releases, streaming, and frame coordinates are not altered by this. A repetition can assign appropriate media that have arrived in the meantime.

Immutable `StudyTableImport` receipts store the source and mapping hash, mapping identity, and a validated pseudonymous LX snapshot. An identical source identity with differing content is a conflict; corrections require a separate, verified correction workflow. New source identities append findings; existing, unaffected findings remain active. Import writers are serialized at the center level, finding writers at the examination level. Unique database constraints provide additional protection for receipt identity.

The selected cohort receives a reference to the import dataset. Its examination memberships determine the imported scope. During the preview, these are unified with examinations from linked datasets; study filters, eligibility criteria, and current center rights still apply. Imported examinations may appear without media. Without imported references, the existing preview semantics remain intact. References do not create annotations and do not expand permissions.

The baseline mapped-table workflow requires the shared lx-dtypes contract, `xlrd`,
EndoReg migration `0096_studycohort`, and lx-annotate migration
`0015_move_studycohort_to_endoreg`, including their predecessors. The current
lx-annotate import-and-capture workflow additionally requires EndoReg migration
`0105_study_collection_entry` and an already imported, validated full
`PackageSnapshot` for the exact knowledge-base module/version used by each
examination. A registered terminology package or historical graph-only import
receipt alone does not satisfy this requirement. Both validation and committed
imports exercise this capture boundary; imports cannot succeed with missing
collection evidence.

Standalone hub study exchange additionally requires EndoReg migration
`0106_study_transfer`; see the canonical [hub transfer contract](hub_transfer_typed_contract.md#standalone-study-ledger-transfer)
for authenticated receipt and explicit acceptance semantics. Publish matching
lx-dtypes, endoreg-db, and lx-annotate versions together: local source checks do not
replace coordinated release and migration delivery. Runtime bootstrap through
`get_runtime_paths().ensure_directories()` must create the protected mapping
storage directory.

The migration transfers existing direct import assignments into datasets and retains their examinations. No clinical deletion ownership is retrospectively assumed for historical import receipts. Joint delivery of the contracts and both applications is required, since clinical datasets do not have an AI model type. The backward data migration does not remove already created datasets.