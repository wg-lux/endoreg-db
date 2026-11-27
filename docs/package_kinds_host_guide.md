# Package kinds in endoreg-db

This guide describes what endoreg-db does with each knowledge-base package kind
and which operator path installs it. The kinds themselves, their allowed
contents and dependencies, and how to choose one are defined by lx-dtypes in
`docs/guides/package-kinds.md` of the lx-data-models repository. That document
is canonical; this one only covers host behavior.

Packages are versioned, digest-verified artifacts registered in the
terminology registry below `TERMINOLOGY_ROOT`. Registering a package never
changes the database by itself. Each kind reaches the database through one
explicit path.

## What happens to each kind

| Kind | How it is installed | What changes in the database | Activation |
| --- | --- | --- | --- |
| `application_data` | `setup_deployment` role `reference_packages` | Reference catalogues are planned and projected into host tables (centers, labels, genders, AI model metadata, medications, workforce, sustainability, ...). Existing equivalent rows are reused; conflicting rows block the whole apply. | Never activated |
| `terminology` | `setup_deployment` role `terminology_packages` (validated and locked); clinical rows through `import_clinical_reference_data` | Nothing during deployment. `import_clinical_reference_data` projects the clinical graph (examinations, findings, classifications, indications, units, ...) and records a `ClinicalReferenceImport` receipt. | Not activated for reporting |
| `report_templates` | `setup_deployment` role `report_template_packages`, or a registry import | Nothing during deployment. Its dependency closure can be projected with `import_clinical_reference_data` like terminology. | At most one package may set `activate: true`; it becomes the active registry selection after the database commit |
| `study` | `setup_deployment` role `study_packages` (registered only); presets with `import_study_preset --module --module-version` | `import_study_preset` creates the preset's examination types and examinations and requires referenced findings and indications to exist already. | Never activated by setup |

Study protocols (`StudySetupDefinition` YAML, imported with
`import_study_setup`) are not a package kind. They reference terminology
packages through `terminology_requirements`.

## Kind checks in the host

- `setup_deployment` fails closed when a selected package's registered kind does
  not match its role (`DeploymentPackageKindError`). Version 1.0 setup documents
  must be migrated with `setup_deployment migrate <file> --output <file>` before
  planning.
- `validate`, `plan` and `apply` validate every catalogue and every selected
  clinical graph before any write. `apply` requires the reviewed lock and its
  own outermost transaction; registry activation happens only after the
  database commit and is reported separately if it fails.
- Registry imports and registered reads run the lx-dtypes package lint. A
  package whose own records do not belong to its kind
  (`package_kind_content_violation`) is rejected, so application data can no
  longer carry clinical copies.
- `import_clinical_reference_data` and `import_study_preset` take an exact
  `--module` and `--module-version`; they do not select by kind. Choose the
  package whose clinical graph you need.

## Recommended order on a new site

1. Register the packages (shipped catalog or registry import).
2. Write a v2 deployment document: `application_data` in `reference_packages`,
   terminology in `terminology_packages`, report templates in
   `report_template_packages` (one with `activate: true`), studies in
   `study_packages`.
3. Run `setup_deployment validate`, then `plan --lock-file ...`, review the plan,
   then `apply` with the same lock.
4. Project the clinical graph of every package that examinations will reference:
   `import_clinical_reference_data --module <name> --module-version <version>`.
   Run `--dry-run` first; conflicts are listed and nothing is written.
5. Import study presets and study setups that build on those packages.

## Why the projection receipt matters

Study evidence capture (table import, study exchange, single-examination
collection) reads each examination's structured ledger and requires a
`ClinicalReferenceImport` receipt for the ledger's exact knowledge-base module
and version. Without it, capture fails with "Install the exact ledger
terminology package before capturing study evidence". Run step 4 for the
package a study's examinations are recorded with, not only for the active
report-template package.

## Conflicts

`import_clinical_reference_data` compares each package row with existing host
rows. Rows that only need binding to an equivalent legacy row are resolved with
`--adopt-existing`. Rows whose clinical content differs (descriptions, linked
classifications, choices, descriptors) remain conflicts and block the import
without writes. Do not resolve them by editing rows by hand: a differing
clinical meaning needs a reviewed package change in lx-dtypes, published as a
new package version.
