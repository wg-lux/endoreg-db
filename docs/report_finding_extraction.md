# Finding presence from imported report text

Owners: endoreg-db and lx-dtypes maintainers. Requirements and verification
evidence: [Reporting.yml](../feature-tracking/Reporting.yml),
criterion `yaml_finding_extraction`.

PDF/TXT report imports and the SAP watcher use the same service,
`services.reports.finding_extraction`. It stores mentions under
`RawPdfFile.raw_meta.finding_extraction`, with canonical finding names,
`present`, `absent`, or `uncertain` presence values, and character offsets in
the anonymized text. This evidence does not constitute confirmed PatientFinding
records. Unmentioned findings remain unknown; empty evidence does not mean
that all findings have been ruled out.

The examination supplies the module and version when available. Otherwise,
the active selection in the configured terminology registry applies. Without
a registry or an active selection, this optional enrichment is disabled.
An incomplete examination identity, an invalid selected package, or invalid
rules abort the import. Packages without rules preserve existing import behavior.

## YAML configuration

An existing `model: finding` entry can contain `text_extraction`.
Each rule contains `presence` and a spaCy Matcher `pattern`. The canonical
identity comes exclusively from the finding's `name`; display names are not
stored as identities.

An executable example is available in
[finding_extraction.yml](examples/finding_extraction.yml). The extraction
tests load it through a real terminology registry.

This limited configuration example is not a complete clinical negation model.
Negation, uncertainty, historical statements, and language variants must be
explicitly covered by the respective study package. A longer match suppresses
shorter matches contained within it for the same finding. Conflicting matches
of equal length remain separate pieces of evidence. Neither unknown words nor
unconfigured negations are interpreted.

The file belongs in the existing package structure and must be included through
`config.yaml -> data.files`. Registration and selection use the existing
module/version mechanism, without hard-coded checkout paths or automatic
changes to published packages.
See [YAML package import](wiki/dataloader_yaml_authoring.md#versioned-presets-from-shared-terminology-storage).
`load_base_db_data` remains responsible for regular reference-data provisioning;
it does not activate extraction rules.

## FHIR

The existing lx-dtypes method can use the same extractor:

```python
from endoreg_db.services.reports.finding_extraction import extract_finding_mentions

result = knowledge_base.import_terminology_validated_fhir_observations(
    observations,
    text_extractor=extract_finding_mentions,
)
```

Known LX finding codes are resolved independently of translated display names.
For other codes, `code.text` can explicitly be evaluated using the YAML rules.
The existing FHIR profile still requires `coding` with `system` and `code`.
Components are rejected for text extraction because their assignment to multiple
detected findings would be ambiguous. `extraction_mentions` also includes
negative and uncertain mentions, along with the index of the original
Observation. Mentions from the same Observation become `reported_findings`
only when they are exclusively positive.

## Confirmation in the anonymization view

The `finding_suggestion_review` criterion in
[Reporting.yml](../feature-tracking/Reporting.yml) describes finding review.
The lx-annotate component `AnonymizationValidationComponent.vue` displays
import evidence and image AI suggestions through
`media/pdfs/<id>/finding-suggestions/` and
`media/videos/<id>/finding-suggestions/`, respectively. Reading requires
`patient:read` and access to the center. Inconsistent terminology returns HTTP 409.

Videos provide suggestions only from model-linked segments or positive frame
classifications. The `annotation_mappings.yml` file in the selected terminology
package must have the same module/version identity. Its existing
`coloreg_annotation_mapping_v1` schema links `label`,
`modalities` (`frame`/`segment`), and `finding_name`; `labelset_names` restricts
the permitted model label sets. Without a matching mapping, no suggestions
are produced. Interventions are not automatically adopted.

Only positive suggestions with a linked examination can be explicitly accepted.
Negated or conflicting evidence remains visible but cannot be accepted.
Acceptance uses the existing PatientFinding endpoint with its write permissions
and a stable `instance_id`; confirming the same suggestion again does not create
a second finding. Multiple matches for the same finding name produce one
suggestion, not an automatic lesion count. Anonymization approval remains a
separate action.

## Deployment

The matching lx-dtypes version containing the new contract must be deployed
together with the backend and the matching lx-annotate interface.
spaCy uses the existing configured model manager from lx-anonymizer.
The matcher and document are scoped to a single invocation. An import stores
evidence in its publication transaction; reprocessing replaces the evidence
object. Previously completed imports reused without changes are not
automatically analyzed again.

## Persisted report-concept word embeddings

The `report_concept_embeddings` criterion in
[Reporting.yml](../feature-tracking/Reporting.yml) owns this feature.
`ReportConceptEmbedding` stores an explicitly annotated **anonymized report text
span**, its vector, and a foreign key to one persisted `PatientExamination`,
`PatientFinding`, or `PatientFindingClassification`. Classification rows also
snapshot the selected choice. Report, patient examination and center must agree.

Only `RawPdfFile.anonymized_text` is an embedding input. The report must have
`state.anonymized` and `state.anonymization_validated` set. Original `text`, images,
video, concept labels and externally supplied vectors are not capture inputs.
There is no fallback when anonymized text is absent. A report created from PDF,
TXT, CSV or FHIR follows the same boundary after textual import and anonymization.

Capture takes a `ReportConceptSpan` from `lx_dtypes.models.contracts.text_embedding`:
report ID, SHA-256 of the current anonymized text, Python character offsets
(start inclusive, end exclusive), concept kind and **persisted annotation ID**.
The phrase is read from the locked report, never from caller-supplied text.
Spans must contain 1–2000 characters. The stored concept link is an explicit
alignment; report-level co-occurrence does not establish word-level synonyms.
Existing annotation workflows can call `capture_report_concept_embedding` once
such an alignment is known, supplying their authorized report queryset.

An embedding retains model identity, revision, preprocessing identity and dimension.
Vectors must be finite, nonzero and dimensionally consistent. Embeddings use
`de_core_news_lg` 3.8.0, a locked EndoReg dependency installed from explosion's
release wheel (`[tool.uv.sources]` in `pyproject.toml`; `uv sync` installs it, no
runtime download). `default_report_text_embedder()` loads it once per process and
checks its language, version and 300-dimensional static vectors before use.
`SpacyReportTextEmbedder` averages static word vectors (`spacy-doc-vector-v1`) and
rejects pipelines without them, including lx-anonymizer's bundled
`de_core_news_sm`. That small model remains lx-anonymizer's anonymization NER model
and the finding-extraction tokenizer; it is not an embedding model. Empty/OOV vectors
fail. No text is sent to an external provider. A compatible local encoder can
implement `ReportTextEmbedder` while keeping the same report-only capture boundary.

`de_core_news_lg` is trained on general German text, not clinical language. On a
small check of clinical synonym/distractor triples it ranked the synonym higher in
only 6 of 11 cases (the small model: 5 of 12), so similarity rows are weak leads
for review. A clinical embedding model would need its own evaluation. Changing the
model starts a new embedding space; earlier vectors stay valid but are not mixed in.

The service serializes captures on the report row. Repeating the same source,
span, target and embedding space returns the existing row without inference.
Changing the source or model revision creates a distinct identity. Stored evidence
is not edited to follow a changed report or reassigned annotation: export rejects
stale source hashes, changed concept snapshots, unvalidated reports and inactive
annotations. Deleting a report or linked annotation cascades to its vectors.
Database bulk writes bypass model validation and must not be used for capture.

After installing the matching lx-dtypes version and applying migration
`0110_report_concept_embedding`, the management command supports:

```bash
# Obtain the source hash without printing report text.
python manage.py report_concept_embeddings source --center-id 1 --report-id 42

# Supply one ReportConceptSpan JSON object on stdin. Uses de_core_news_lg;
# add --spacy-model <installed package or directory> to choose another local model.
python manage.py report_concept_embeddings capture --center-id 1 < annotation.json

# Export the closest annotated spans within a center (optional --report-id).
python manage.py report_concept_embeddings export --center-id 1 --top-k 20 > similarities.csv
```

All operations also support `--database`. For the JSON request, `concept_kind` is
`examination`, `finding` or `classification`; `annotation_id` references the respective
patient annotation row, not its terminology definition. No embeddings are generated
automatically from every word in a report. Capture requires explicit
source-to-concept alignments.

Similarity export compares compatible model spaces and matching KB identities.
It includes both phrases, annotation IDs/kinds, concept and choice names, source
hashes/offsets, model provenance and cosine similarity. Results are candidate
associations, not calibrated clinical probabilities or automatically approved KB
synonyms. It performs bounded exact comparisons over at most 1000 selected vectors,
with 1–100 neighbors per vector. Vectors use portable JSON storage; an approximate
vector index is not required for this initial corpus. The command validates all
selected evidence before writing CSV. Exported files contain anonymized report
fragments and remain subject to the report's access and retention policy.
