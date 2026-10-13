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
