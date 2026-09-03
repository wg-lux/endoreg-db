# Code Quality and Maintainability Boundaries

This document describes the repository's executable quality controls. Current
scope and readiness evidence remain in
[`feature-tracking/CodeQuality.yml`](../feature-tracking/CodeQuality.yml).

## Dead-code process

`devenv tasks run quality:dead-code` runs Vulture with at least 90 percent
confidence against `endoreg_db` and `scripts`. Migrations, tests, settings
modules, management commands, and Django registration modules are excluded from
automatic deletion because they are often referenced dynamically.

Reviewed exceptions live in `quality/dead_code_baseline.yml`. Each entry records
the location, tool message, confidence, classification, rationale, owner, and
review date. The guard fails when a new finding appears, a baseline entry becomes
stale, or an exception review expires.

The same file records separately investigated `deletion_candidates`, including
path and line range, classification, consumer evidence, risk, recommended
action, owner, and review date. This is not a Vulture allowlist:
`confirmed_dead` means scheduled removal, while `compatibility_contract` and
`uncertain` require more consumer evidence before deletion.

A static finding alone never authorizes deletion. Review leaf and barrel
imports, string imports, Django registration, URLs, signals, jobs, commands,
package exports, and cross-repository consumers first. Public contracts need a
deprecation path before removal.

## Quality boundary guard

`devenv tasks run quality:boundaries` freezes the reviewed legacy inventory of
broad `Exception` and `BaseException` handlers, bare `except:` clauses, and type
suppressions. The versioned baseline is
`quality/quality_boundary_baseline.yml`. Its fingerprint is based on file,
qualified scope, and rule rather than line number, so line movement is stable;
new, removed, or relocated findings require review.

The baseline is neither an allowlist nor an assertion that existing findings
are desirable. Update it only as part of a named cohort:

1. Prove broad handlers are genuine HTTP, command, job, storage, or integration
   boundaries, or replace them with concrete exception types.
2. Prefer a narrower framework adapter or correct annotation over a type ignore.
   A necessary ignore needs a reason, owner, and retirement path.
3. Change counts and fingerprints only after review; do not extend a review date
   without reviewing the finding again.
4. Run Pyright, the boundary guard, and focused failure-path tests.

## Reproducible quality run

`devenv tasks run quality:code-regression` runs Pyright, the dead-code guard,
the boundary guard, and the fast Pytest marker lane in the synchronized project
environment. Refresh dependencies first with `devenv tasks run agent:sync` when
needed. The task intentionally does not start a nested `test:sync`.

`devenv tasks run test:fast` remains the independent developer and pull-request
lane. It synchronizes test dependencies and uses the same markers, environment
boundaries, parallelization, and database reuse as the regression task.

## Layer and exception boundaries

- Pure transformations are typed, side-effect-free functions.
- Database, network, and filesystem operations belong in services or explicit
  integration boundaries.
- Django models own persistence, constraints, and thin state transitions, not
  workflow orchestration or new service imports.
- Helpers have a domain owner and do not become competing catch-all modules.
- New barrel imports and import cycles are prohibited.
- Domain and service code uses small typed error hierarchies. HTTP, command,
  job, and integration boundaries translate them centrally into status, exit
  code, retry classification, and structured logs.
- Security, storage, cryptographic, and clinical invariants fail closed. Error
  responses and logs exclude secrets, master keys, direct patient identifiers,
  and complete payloads.

## Configuration boundary

The parsers in `endoreg_db.config.env` use a default only when a variable is
absent. A present but invalid value raises `EnvironmentValueError`. Boolean
values accept only `1`/`0`, `true`/`false`, `yes`/`no`, and `on`/`off`, without
case sensitivity. Integers must be accepted by `int()` and safety-sensitive
callers enforce explicit lower bounds. Floating-point values must be finite.
`env_choice` normalizes closed sets of mode values. Unknown or empty modes do
not silently become `celery`, `inline`, or another default.

Python loads `.env` only for the repository's `dev` and `case_gen` settings,
always with `override=False`. Test, production, and embedded-consumer settings
do not load it as a library import side effect. Devenv, Secretspec, or the
process supervisor supplies their environment before Python starts. An
explicitly blank path variable is invalid; only an absent variable may use its
documented default.

The debug snapshot redacts broker addresses, database names, storage and staging
paths, and the repository path. `DOTENV_LOADED` means a development file was
actually loaded. Configuration exceptions contain only the variable name and
expected type, never the raw configured value.

The first migrated exception cohort is DICOM/FHIR interoperability:

- `endoreg_db.exceptions` owns domain error codes, safe messages, audit reasons,
  and retry classifications.
- Services map expected validation and integrity failures to these types while
  retaining the internal cause through exception chaining.
- `endoreg_db.views.interoperability_errors` owns HTTP translation and does not
  expose internal messages.
- Unknown errors are logged at the integration boundary and re-raised unchanged.
  They are not reclassified as client errors.

For expected invalid export data, the public FHIR error shape is HTTP 422 with
`code`, `detail`, and `retryable`. Rollback removes the view mapper; abort on
unexpected reclassification or exposure of internal details.

The second migrated cohort covers command and job boundaries:

- `backfill_dicom_manifest_v2` translates its typed failure through
  `endoreg_db.management.command_errors` into exit code 1 and a stable,
  data-minimized code and message.
- `MediaOperationDeferred` centrally defines retry classification. Its three
  Celery tasks use at least a 60-second delay and at most 20 attempts, and log
  only job name, hashed object identity, error code, and retry parameters.
- Other or unknown job failures are not retried automatically and propagate to
  Celery unchanged.

For every further cohort, name the owner, affected public contracts, metrics and
logs, abort criteria, and reversible rollback before deployment. Baseline
reductions survive rollback unless an explicitly removed compatibility contract
must be restored; increasing the baseline requires another review.

## Review checklist

1. Were static, dynamic, and cross-repository consumers checked?
2. Is the code in its owning layer?
3. Are inputs and outputs concretely typed?
4. Are side effects confined to a visible boundary?
5. Is a concrete exception translated only at its owning boundary?
6. Are chaining, auditability, and fail-closed behavior preserved?
7. Do Pyright, import-boundary, runtime, and failure-path tests cover the change?
