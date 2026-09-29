# Dataloader YAML Authoring Guide

## Configure a deployment with one setup file

`setup_deployment` is the operator entry point for a site and its exact package
selections. Its shared versioned contract is `DeploymentSetup` in
`lx_dtypes.models.contracts.deployment_setup`; the canonical field rules are in
the [package structure guide](https://github.com/wg-lux/lx-data-models/blob/main/docs/guides/dtypes-package-structure.md#deployment-setup-v1).
Implementation and operational readiness remain recorded in
[DtypesStudyDefinitions.yml](../../feature-tracking/DtypesStudyDefinitions.yml).

This command requires the installed `lx-dtypes >=0.4.0` package, as declared in
`pyproject.toml` and pinned by `uv.lock`. A sibling source checkout used by
Pyright is not a runtime dependency. Verify deployment tests with the installed
package, without a `PYTHONPATH` override.

Start with a shipped template and edit the site's real immutable key and display
name. Templates are configuration starting points, not patient records or
permission grants:

```sh
python manage.py setup_deployment template minimal --output setup.yml
python manage.py setup_deployment schema --output deployment_setup.schema.json
```

The available templates are `minimal`, `coloreg`, `training`, and
`green_endoscopy`. `minimal` provisions only the site; its validate, plan and apply
steps need no registry when both package lists are empty. `training` selects an
independent training center plus `endoreg_reference` and `endoreg_workforce`;
it does not import the synthetic authoring package `training_preset`. Inspect
package selections before use. Workforce reference definitions do not create
employees or assignments, and training does not populate patient examples.
Template and schema export refuse an existing output file; choose a new path
when exporting again.

For YAML editor completion, associate the generated `deployment_setup.schema.json` with
`setup.yml`, or add this comment to the setup file:

```yaml
# yaml-language-server: $schema=./deployment_setup.schema.json
```

The schema is generated from the same Pydantic contract used at runtime. Editor
validation does not replace registry resolution or database reconciliation.

### Validate, review, apply

Deploy compatible lx-dtypes and endoreg-db versions and apply migrations first.
Provisioning the shipped registry is a separate explicit step:

```sh
python manage.py migrate
python manage.py setup_deployment provision
python manage.py setup_deployment validate --file setup.yml
python manage.py setup_deployment plan --file setup.yml --lock-file setup.lock.yml
python manage.py setup_deployment apply --file setup.yml --lock-file setup.lock.yml
```

`provision` uses the existing governed terminology storage. It can initialize
the default active bundle when the registry is initially empty. It does not
project clinical tables. Register custom packages through the existing governed
terminology import workflow before selecting them in a setup file.
Sources must already be available locally through the governed registry; remote
source URLs are rejected. Site-only setups can omit `provision` entirely.

`validate` checks the setup and resolves its exact registered packages.
`plan` reconciles the selected catalogues and site against the current database,
reports additions, reuse and conflicts, and resolves content digests. It does
not change database rows or registry selection. Supplying `--lock-file` writes
the explicit review artifact; omitting that option previews without creating it.
Review both the plan and the generated lock before applying.
The lock contains the setup and selected packages, each with `sources` entries
for the root and dependencies (`module`, `version`, `content_sha256`). Its package
digest covers the complete resolved source closure. A successful plan can replace
an existing lock file; a conflicting plan exits unsuccessfully without writing
one. The lock path must differ from the setup path.

`apply` requires that lock, checks that it belongs to the current setup, resolves
the same content again and repeats conflict checks. Changing YAML or package
content requires a new plan and review. Retain the setup and lock alongside
deployment records; neither is a substitute for a database/registry backup.
There is no force-overwrite option or fallback to old host fixtures.
For an existing installation, set `adopt_existing: true` explicitly only after
reviewing the plan. Adoption retains equivalent rows and their primary keys;
different fields or relationships remain conflicts. The default is `false`.

The site's `center_key` is immutable. Applying the setup creates or reuses the
matching center and explicitly selects it through `ApplicationSettings.center`
for local intake. A conflicting existing key/name is an error. This selection
does not change a remote sender's center or replace explicit study ownership.
An empty package selection still permits independent local center provisioning.

Reference packages project their complete catalogue dependency closure.
Study packages resolve and pin their definitions; `activate: true` selects the
registry's active knowledge base. It does not create study membership, endpoints,
datasets, employees, presets or every clinical graph row. `activate: false`
leaves the existing selection unchanged; it is not a deactivation request.
Clinical graph, preset and employee imports below retain their explicit roles.

### Transaction and recovery boundary

Center selection and catalogue changes commit in one database transaction.
Registry activation follows that commit and is a separate filesystem operation.
An activation failure can therefore leave the database provisioned while the
command returns failure. A rejected concurrent registry revision leaves the
existing active selection unchanged. Inspect the current registry and command
error, review a fresh plan where required, and retry the idempotent setup with
matching content. Do not delete imported rows or rewrite receipts to retry.

For deployment rollback, restore the database, registry and immutable package
artifacts together. Removing a package from a setup does not delete its rows,
captured records or historical definitions. Setup commands do not reverse
database migrations or establish clinical approval.

## Versioned clinical provisioning

Clinical reference definitions are owned by `lx-dtypes`. The host projects
validated catalogues into its existing relational tables; it does not execute
model names or Python imports supplied by YAML. Readiness and remaining gates
are recorded only in [DtypesStudyDefinitions.yml](../../feature-tracking/DtypesStudyDefinitions.yml).

The packaged `endoreg_reference@1.0.0` preserves the previous clinical catalogue,
including diseases, medications, laboratory definitions, examination times,
classifications, units and relations. Its host provisioning records also cover
the previous bootstrap's centers, hardware configuration and labels. It contains
no patients, employee names, model weights or active-model selection. Local
center defaults are configured independently of a study or catalogue.

`coloreg@0.2.0` includes this catalogue as a standard package dependency.
`coloreg@0.1.0` remains available with its original content digest. The catalogue
preserves legacy relational meanings; the canonical ColoReg graph remains a
separate typed representation. Conflicting graph definitions cannot overwrite
legacy rows merely because their names match.

Run database migrations first, provision the governed terminology registry,
then reconcile an exact identity:

```sh
python manage.py migrate
python manage.py import_reference_catalog --module endoreg_reference --module-version 1.0.0 --dry-run --format yml --adopt-existing
python manage.py import_reference_catalog --module endoreg_reference --module-version 1.0.0 --adopt-existing
```

The import command requires an already registered package. Registry provisioning
uses the existing `hydrate_shipped_terminology()` / `TerminologyService` workflow;
it does not activate a study. `load_base_db_data` is the compatibility bootstrap:
it explicitly provisions shipped registry entries before importing the pinned
catalogue. Its `--dry-run` variant never provisions registry files. Both module
and version can be selected explicitly. No command imports patient data, creates
user-owned cohorts or changes study activation.

The plan distinguishes creation, equivalent legacy adoption, reuse and conflict.
Adoption retains primary keys and records `legacy_equivalent`; it does not claim
the historical records originated from the newly imported package. Duplicate
identities, changed fields or relations, missing imported rows and changed
content under an existing module/version/projection block the operation. Back up
the database and registry together before adopting an installed database. Resolve
conflicts through reviewed mappings or a separately versioned definition; there
is no force-overwrite or YAML fallback option.

All catalogue rows, foreign keys, many-to-many links and the validated immutable
receipt commit in one transaction. Reimport checks the persisted definitions
instead of silently repairing drift. Reference imports use the same process and
PostgreSQL advisory locks as preset provisioning. An import failure rolls back
its changes; restoring a deployment must restore captured records and their
original receipts together.

The existing `load_unit_data`, `load_disease_data`, `load_medication_data`,
`load_examination_data` and other bootstrap subcommands delegate to catalogue
projections with their complete dependency closure. They preserve public command
names and adopt only equivalent rows. `--source yaml`, `--source hybrid` and the
unpinned indication overlay are removed. Use `--module` and `--module-version`.
For a configurable subset, use the typed catalogue selector:

```sh
python manage.py import_reference_catalog --module endoreg_reference --module-version 1.0.0 --record-type medication --dry-run --format yml --adopt-existing
```

For the canonical clinical graph, use `import_clinical_reference_data` with the
same explicit module/version and dry-run options. Its plan includes field-level
existing/proposed values. Clinical graph imports preserve descriptors, templates
and definition metadata in an immutable graph snapshot. A successful catalogue
import is not evidence that a conflicting canonical graph can be adopted.

## Optional reference packages and authoring

`endoreg_workforce@1.0.0` contains 39 profession, qualification and shift
reference definitions. It contains no employees or assignments.
`endoreg_green_endoscopy@1.0.0` contains 153 sustainability definitions and
uses `endoreg_reference` for its shared units and centers. The corresponding
compatibility commands import only their declared kinds and required relations:

```sh
python manage.py load_profession_data
python manage.py load_qualification_data
python manage.py load_shift_data
python manage.py load_green_endoscopy_wuerzburg_data
```

Author `.yml` records using the strict `reference_catalog` contract in the
[shared package guide](https://github.com/wg-lux/lx-data-models/blob/main/docs/guides/dtypes-package-structure.md).
Each field, nullable value and relation is declared in `lx_dtypes`.
Unknown fields, duplicate identities and dangling relations fail validation.
Green Endoscopy records without names use declared composite natural keys;
quantities are definition content, not identity. Legacy missing weight values
represented as NaN reconcile to null, matching the existing weight model.
Other non-finite numbers are rejected.

The raw Python APIs `endoreg_db.utils.dataloader` and
`endoreg_db.utils.yaml_model_loader` have been removed. Replace external callers
with the public management commands or `services.reference_catalog` using an
already validated `ReferenceCatalogSnapshot`. Arbitrary fixture directories,
Python model names and warning-and-skip imports are no longer supported.
`helpers.data_load_orchestrator` retains the existing command wrappers.
The separate `lx_dtypes` package resolver remains the supported YAML reader.

The old qualification/shift commands had incorrect source-directory wiring.
The versioned package carries the intended existing source definitions. An
installation populated by those broken paths may need reviewed reconciliation;
the new importer never silently rewrites an inconsistent row.

Deploy the matching lx-dtypes code and packaged data together with endoreg-db,
then apply the host migrations and provision the registry. An older installed
lx-dtypes release without the reference-catalogue contracts is incompatible.
Existing source fixtures are no longer an import fallback.

## Versioned presets from shared terminology storage

The authoritative readiness record for this path is
[`DataLoading.yml`](../../feature-tracking/DataLoading.yml), criterion
`optional_center_employees`. The clinical graph migration remains tracked in
[`DtypesStudyDefinitions.yml`](../../feature-tracking/DtypesStudyDefinitions.yml).

For centers, genders, labels, examination references, and optional employees,
author standard lx-dtypes packages instead of duplicating host fixtures. The
canonical [package structure guide](https://github.com/wg-lux/lx-data-models/blob/main/docs/guides/dtypes-package-structure.md)
provides a complete directory tree, executable `.yml` examples, validation
commands, field/reference rules, and an agent checklist. Use
the existing terminology provisioning/import workflow to register an exact
module/version in `TERMINOLOGY_ROOT` (the shared
`get_runtime_paths().terminology` directory used by lx-annotate). Then run:

```sh
python manage.py import_study_preset --module YOUR_REGISTERED_MODULE --module-version YOUR_REGISTERED_VERSION
```

The command reads the existing terminology service and does not scan arbitrary
directories, provision a default package, or change the active study. A package
may contain `study_preset`, ordinary `examination`/`examination_type` records,
and optional `center_employee_list` records. See the lx-dtypes
`docs/guides/package_boundary.md` for the typed fields and ownership contract.
Missing clinical findings/indications and ambiguous legacy names abort the
whole import. Provision the clinical graph first; this importer does not
replace the separate dtypes study-definition implementation.

Preset imports create missing definitions and reuse equivalent existing rows.
They reject changes to existing examination, gender, label-type, label, or
versioned label-set fields and relationships, rolling back the entire import.
Use a distinct definition identity for a different meaning; importing another
package must not reinterpret rows already referenced by clinical records.

To add only employees to already provisioned centers:

```sh
python manage.py import_center_employees --module YOUR_REGISTERED_MODULE --module-version YOUR_REGISTERED_VERSION
```

Employee records reuse lx-dtypes `Examiner`/`ExaminerDataDict`, with `center`
referencing the immutable host `center_key`. They are always optional and are
added to existing recognition lists without removing local entries. Without
local names, the report pipeline delegates default configuration to
lx-anonymizer. Existing examiner identity hashes are unaffected.

`load_center_data` now loads center identities only: it neither reads the
packaged first/last-name files nor resets existing center employee relations.
The public load commands remain as versioned catalogue compatibility entry points. Back up the database before changing selected definitions;
restore it to reverse a projection, since reimport does not remove added names.
