# Medical ledger integration contract

## Scope and ownership

Medication and laboratory terminology use the existing canonical
`MedicationReference` and `LabValueReference` catalog records. The standard
dtype loader validates them, including finite, ordered normal ranges and
non-negative precision/adjustment fields. Patient-scoped
`GET /api/patients/{id}/medical-terminology/` returns these persisted references
and units for case setup.

Medication records and writes accept optional timezone-aware `start_at` and
`end_at`. An end requires a start and cannot precede it; the database also
enforces ordering. PATCH validates the merged period. The aggregate ledger
POST may include `case_id` (the numeric case primary key), atomically linking
only its newly created children to that patient's open, nondeleted case.
Omitting the case preserves patient-only creation. Existing idempotency keys
remain compatible with omitted additive period/case fields.

The case setup medication page creates medication periods and timestamped
numeric or text lab measurements using those references. Completed examination
snapshots copy selected rows; see [case graph persistence](case_graph_persistence.md)
for alternative drafts, selection, follow-ups and immutable research exports.

The medical ledger is a patient-scoped interchange view over canonical
`endoreg_db` tables. It never creates patient demographics. Every read or write
starts from an already existing, center-visible `Patient`; the patient primary
key in an aggregate request must match the patient in the URL.

`lx_dtypes.models.ledger.medical` owns strict request/response validation,
snake-case JSON schemas, serialization, and deterministic ledger UUID
derivation. `endoreg_db` owns Django models, foreign keys, deletion behavior,
transactions, authorization, query planning, and migrations. Orchestration
belongs in `endoreg_db.services.interoperability.medical_ledger` and the patient API actions, not
in either repository's model classes.

The patient medical ledger is not a second persistence store. Its
`external_ids["endoreg_db"]` values identify canonical rows, and its UUIDs are
deterministically derived from those immutable model/primary-key identities.
Terminology labels are snapshots for interchange; foreign keys remain the
database source of truth.

## Field inventory

| Ledger record | Canonical model and cardinality | Required and optional data | Relations and deletion | Time semantics | Provenance and API representation |
| --- | --- | --- | --- | --- | --- |
| `PatientDisease` | `PatientDisease`; patient 1:N diseases | Required: patient, disease. Optional: classification choices, start/end date. JSON objects: numerical descriptors and subcategories. | Patient and disease use `CASCADE`; classification choices are M:N. Write references are terminology names and must belong to the selected disease. | Calendar dates; end cannot precede start. `last_update` is database-generated. | Row identity is `PatientDisease:<pk>`; response contains deterministic UUID and terminology-name snapshots. |
| `PatientEvent` | `PatientEvent`; patient 1:N events | Required: patient, event, start date. Optional: end date, description, classification choice. JSON objects: subcategories and numerical descriptors. | Patient/event and optional choice use `CASCADE`; a supplied choice must belong to the selected event. | Calendar dates; end cannot precede start. `last_update` is database-generated. | Row identity is `PatientEvent:<pk>`; clinical description is payload data and must not be logged. |
| `PatientLabSample` | `PatientLabSample`; patient 1:N samples | Required: patient, sample type, aware sample datetime. Nested values default to an empty list. | Patient and sample type use `CASCADE`; values reference the sample through `PatientLabValue.sample`. | Timezone-aware datetime; no implicit timezone fallback. | Row identity is `PatientLabSample:<pk>`; nested values carry their own canonical identities. |
| `PatientLabValue` | `PatientLabValue`; patient 1:N direct values and sample 1:N nested values | Required: lab terminology, aware timestamp, and exactly one numeric or text value. Optional: unit and sample. Normal range is a strict object. | Patient, lab terminology, sample, and unit use `CASCADE` where present. The service always assigns the route patient, including nested values. | Timezone-aware measurement datetime. Numeric values must be finite. | Row identity is `PatientLabValue:<pk>`; direct and nested projections reuse the same UUID instead of inventing copies. |
| `PatientMedication` | `PatientMedication`; patient 1:N medications | Required: medication and active status. Optional: indication, unit, JSON dosage; intake times default empty. | Patient, medication, indication, and unit use `CASCADE`; intake times are M:N. References are resolved by unique terminology names. | Optional timezone-aware start_at/end_at; an end requires a start and cannot precede it. | Row identity is `PatientMedication:<pk>`; dosage is returned but excluded from audit/log labels. |
| `PatientMedicationSchedule` | `PatientMedicationSchedule`; patient 1:N schedules | Required: patient; medication membership may be empty. | Patient uses `CASCADE`; medication membership is M:N and every member must belong to the same patient. | `created_at` and `updated_at` are timezone-aware and database-generated. | Row identity is `PatientMedicationSchedule:<pk>`; aggregate creates reference newly created medications by zero-based request index. |
| `PatientMedicalLedger` | Computed aggregate, not a Django table | Required: patient. All six child collections default empty. | The URL patient is authoritative. A child cannot select or create another patient. | Child semantics apply; aggregate creation time is not persisted separately. | Identity is `Patient:<pk>`. GET rebuilds solely from canonical tables; POST returns the reloaded, revalidated aggregate. |

## Write and merge semantics

`POST /api/patients/{id}/medical-ledger/` is atomic create-only behavior for
the supplied children. Empty or omitted lists create nothing and never delete
existing rows. It is not replace or merge. Medication and schedule
single-resource routes are explicitly named create or patch operations; patch
distinguishes omitted fields from explicit null where the host schema permits
null.

The aggregate service resolves every terminology and relation before the
transaction commits. Unknown, ambiguous, mismatched, or foreign-patient
references fail the request. A late child failure rolls back earlier scalar
rows and M:N membership.

## Idempotency and concurrency

Every aggregate `POST` requires an `Idempotency-Key` header containing a
non-empty value of at most 255 characters. The identity is scoped to the URL
patient. EndoReg stores a durable receipt with a canonical SHA-256 hash of the
validated create payload and a manifest of canonical EndoReg row IDs. The
receipt is provenance, not clinical shadow persistence: it contains no
diagnosis text, laboratory result, dosage, or serialized ledger response.

The first committed request returns HTTP 201 and
`Idempotency-Replayed: false`. An identical replay returns HTTP 200,
`Idempotency-Replayed: true`, and a ledger projection bounded to the receipt's
row manifest. Reusing the key for a different validated payload returns the
stable HTTP 409 `idempotency-conflict`; a missing or malformed key returns the
stable HTTP 422 `idempotency-key-required`.

The service uses one database transaction at the configured isolation level
and locks the patient row with `SELECT ... FOR UPDATE` before checking the
receipt or writing children. This deliberately serializes aggregate writes for
one patient while allowing different patients to proceed independently. A
database uniqueness constraint on `(patient, idempotency_key)` is the final
race guard. Recognized transient database-lock, deadlock, and serialization
failures receive at most three attempts with bounded backoff. Other integrity
failures are not silently retried.

The receipt is inserted in the same transaction after canonical rows and
Many-to-Many membership exist. The manifest-bounded response is then rebuilt
and validated before the transaction exits. Therefore failure at any logical
disease, event, sample, value, medication, schedule, receipt, or projection
step rolls back the entire graph, all join rows, and the receipt. No
in-progress receipt can survive a failed write.

## Contract version, rollout, and recovery

EndoReg declares the compatible published package range in `pyproject.toml`
and pins the resolved release in `uv.lock`; the contract test requires the
installed distribution to match that lock exactly. The current EndoReg lock is
`lx-dtypes==0.2.15`, while the `0.2.16` sibling source and lx-annotate consumer
lane verify forward compatibility before the next lock refresh. The local
`MedicalLedgerWriteReceipt.record_ids` persistence contract is
`schema_version: "1.0"`. Version 1.0 adds only the explicit discriminator to
the previously unversioned row-ID manifest; its clinical meaning and public
medical-ledger API shape are unchanged.

During the one-release mixed-version window, the version 1.0 reader accepts a
missing discriminator as the legacy version 1.0 shape. Every normal model save
then writes the complete canonical versioned shape. Writers never emit the
legacy shape. An explicit value other than `"1.0"`, an unknown field, or an
invalid row-ID value fails closed at the model boundary and is not silently
rewritten.

Deployment order and gates are:

1. deploy the pinned reader/writer and run the receipt validation report before
   enabling writes;
2. abort deployment if any row has an explicit unsupported version or fails
   strict validation, reporting only the model label, receipt primary key,
   observed version, and validation category;
3. backfill legacy rows by loading and saving each receipt in an atomic batch;
4. rerun validation and require zero unversioned or invalid rows before ending
   the one-release compatibility window.

The report is a dry run by default and contains counts only:

```bash
python manage.py backfill_medical_ledger_receipts_v1
python manage.py backfill_medical_ledger_receipts_v1 --apply
```

`--apply` runs in one database transaction. Any invalid receipt aborts and
rolls back the complete batch; its error contains only the model label, receipt
primary key, observed version, and validation category. The invalid row remains
in place as the fail-closed review record rather than being silently corrected,
discarded, or copied into a second clinical persistence store.

The table is introduced by migration `0058_medicalledgerwritereceipt`, so a
fresh deployment has no receipt backfill. Rollback before any new aggregate
write reverses migration 0058. After writes begin, rollback keeps the table and
returns to the previously deployed application while version 1.0 remains
read-compatible; destructive migration reversal is prohibited until the
receipts and their canonical medical rows have been backed up and reconciled.
Injected failures at every persistence step prove atomic database rollback,
and replay plus parallel-request tests prove idempotent mixed operation.

## Examination and case boundary

`PatientMedicalLedger` owns longitudinal patient medical rows. `Case` and
`PatientExamination` remain EndoReg workflow records and are not copied into
the aggregate. A case may reference canonical lab, medication, schedule, and
examination rows through its existing relations. Those links are references,
not medical-ledger snapshots. SAP IS-H import may establish these relations,
but the general aggregate create route does not infer a case or examination.

## Wire contract

Python models use snake case. JSON consumers may apply their normal
snake-to-camel transport mapping, but the backend contract and generated JSON
schema remain snake case. Dates use `YYYY-MM-DD`; datetimes require an ISO 8601
timezone offset. Null is accepted only for documented optional fields.
Unknown fields are forbidden. Database-generated primary keys, timestamps,
external IDs, and ledger UUIDs appear only in responses and are never trusted
from create requests.


## Reporting clinical import contract

Scope and acceptance are governed by
[MedicalLedgerIntegration.yml](../feature-tracking/MedicalLedgerIntegration.yml),
criterion `reporting_clinical_import_adapters`, and lx-annotate's
`reporting_clinical_export_import` criterion. This section defines the intended
integration contract; it does not add an import endpoint or establish readiness.

The destination is the reporting flow's **Medikation und Laborwerte** page.
The frontend owns selection, preview and review. `lx_dtypes` owns shared typed
clinical exchange contracts and terminology definitions. `endoreg_db` owns
source adaptation, authorization, identity reconciliation, transactions and
canonical clinical rows. Imported data must remain available to the existing
case/examination selection and snapshot workflow.

### Adapter boundary

Use one pipeline: source decoding, versioned adapter validation, typed clinical
normalization, identity and terminology resolution, read-only preview, reviewed
atomic commit. Each adapter declares its supported input versions and clinical
record kinds. YAML describes source columns, null markers, code dictionaries,
timezone policy and terminology mappings; typed functions implement transforms.
Unknown statuses or lossy mappings produce actionable issues instead of defaults.
Adding an adapter must not introduce a separate persistence implementation.

- **MEONA tabular:** reuse the existing document-template recognition and column
  mappings only after checking their coercion and identity rules against the
  export. Product/component identity, event identity and order identity are
  separate concerns. Preserve original dosage and status without inventing
  clinical interpretations.
- **FHIR R4 JSON:** validate supported resource structures and declared profile
  versions. Resolve bundle/fullUrl and contained references locally, rejecting
  ambiguous or unresolved required references. Do not fetch external references.
  Preserve original FHIR key spelling: upload the file as opaque content rather
  than passing its resource dictionary through axios snake-case conversion.
- **BMP XML:** select the parser and schema by the declared BMP version; use a
  hardened XML parser and bounded input. A DataMatrix decoder, when supported,
  feeds the same XML adapter. Retain plan identity, membership, original text and
  structured dosage without turning planned/reported use into administration.

FHIR and BMP are different wire formats. The MII medication model provides the
semantic distinctions: `Medication`, `MedicationStatement`, `List`,
`MedicationRequest` and `MedicationAdministration`. The current medication
period record alone cannot represent every distinction. Extend shared contracts
and canonical persistence before accepting content whose meaning cannot be
represented; do not hide unvalidated source dictionaries in dosage JSON.

Laboratory normalization follows the MII laboratory concepts: `ServiceRequest`,
`Specimen`, `Observation` and `DiagnosticReport`. Preserve local code namespaces
and explicit mappings, quantity comparators, qualitative values, absent reasons,
reference ranges and result status. Effective and issued times are distinct.
Never infer LOINC, UCUM or a unit conversion from a display label alone.

### Review and persistence

The upload may contain multiple patients. Resolve namespaced source identifiers
within the authorized center and show only the matched patient's records in the
case preview. Unmatched or ambiguous identities block the affected import; the
route patient is not a substitute for matching. Do not create demographics.
A source without a case identifier remains patient-level unless the user
explicitly reviews a case association. Record that association's provenance.

The preview returns adapter version, source digest, record kinds, mapping issues
and counts for new, unchanged, corrected and conflicting records. Clinical
preview details are authorized response content, never logs. Any change of
patient, case, file or mapping invalidates the review. Commit revalidates these
inputs and the open-case condition within the transaction. A replay creates no
new rows; a correction requires a source identity and explicit revision policy,
not a new random request key. Preserve superseded facts and reject conflicting
revisions. The existing create-only ledger receipt is reusable infrastructure,
but does not by itself implement source-record reconciliation.

Protected original artifacts, if retained, use encrypted application storage;
parsers consume scoped plaintext materialization. Apply upload, row and resource
limits before expensive parsing. Never retain plaintext exports in repository
fixtures, browser local storage, logs or error responses.

### Observed MEONA export shape

Structural inspection of the supplied March 2026 export established UTF-8
encoding, tab-separated fields, 27,311 rows and these columns:

```text
PatientNr, id, tradename, patient_id, apply_date, prepare_date,
creation_date, actual_dose, unit_dose_name, main_application_id,
main_order_id, status
```

There is no case identifier. Within `(PatientNr, id)`, 10,855 repeated-identity
groups differ in `tradename` or `unit_dose_name`. These are not exact duplicate
rows. The file alone does not establish whether they represent components or
extraction join multiplicity. The existing patient-plus-row-id medication
identity therefore cannot safely distinguish all supplied rows. Do not derive
stable component identity from mutable display names or row positions.

The observed status vocabulary is `0`, `1`, `2`, `3`, `4`; its clinical meaning
requires the source dictionary. All parsed timestamps lack offsets. Application
time has 7,175 null-marker rows and preparation time has 5,137; creation time is
present throughout. Preparation/creation timestamps cannot stand in for an
actual administration time. Source timezone and daylight-saving ambiguity rules
must be verified rather than inherited silently from server settings.

No patient identifiers, medication names or source rows are reproduced here.
Clinical acceptance requires the extraction SQL/data dictionary to establish
identity and status semantics. Synthetic tests can then encode those verified
rules without copying clinical source data.

### Standards references

- [MII Medication 2026 module](https://www.medizininformatik-initiative.de/Kerndatensatz/KDS_Medikation_2026/MIIIGModulMedikation-BeschreibungModul.html)
- [MII Laboratory 2026 profiles](https://www.medizininformatik-initiative.de/Kerndatensatz/KDS_Laborbefund_2026/MIIIGModulLaborbefund-TechnischeImplementierung-FHIR-Profile.html)
- [KBV BMP specification](https://update.kbv.de/ita-update/Verordnungen/Arzneimittel/BMP/BMP_2.8_Q3_2026/EXT_ITA_VGEX_BMP_Anlage3_mitAend.pdf)
