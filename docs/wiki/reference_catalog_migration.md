# One-time colorectal indication reconciliation

| Field | Value |
| --- | --- |
| `owner` | endoreg-db maintainers |
| `readiness` | [DtypesStudyDefinitions.yml](../../feature-tracking/DtypesStudyDefinitions.yml) |

## Scope

| Field | Value |
| --- | --- |
| `migration_id` | colorectal_indication_interventions_v1 |
| `catalogue` | endoreg_reference@1.0.0 |
| `snapshot_id` | sha256:fbe853dbf34c0154940ecfd76787a1d1a769f4a2434e532adf11640aabecf0f9 |
| `specification` | ../../endoreg_db/data_migrations/colorectal_indication_interventions_v1.yml |
| `reported_host` | gc-02 |
| `baseline` | 657 equivalent records, nine missing dependency records, seven conflicting indication relationships. |

### Allowed changes

- If strict reconciliation has no conflicts, perform a normal import or adoption, including a fresh installation.
- Create missing members of the eight named colorectal interventions and histology_sampling.
- Add only those interventions to expected_interventions on the seven named indications.
- Adopt equivalent reference records and write standard immutable catalogue import receipts.

### Preserved

- All existing reference primary keys, descriptions and unrelated relationships.
- Patient observations, historical reports, and their existing provenance or unknown provenance.
- Strict adoption, active terminology selection, and clinical model schema.

## Workflow

### Luxnix

**command:** lx-annotate-load-base-data --reconcile-legacy

#### Behavior

- The shared base-data service invokes reconciliation after Django schema migrations using its prepared runtime and environment.
- The loader provisions the governed registry through the existing hydration helper before invoking reconciliation.
- Fresh installations and equivalent catalogues use the normal strict importer; only the known conflicts use the bounded repair.
- Successful reruns preserve existing IDs and import receipts; failures block dependent service startup.
- The flag requires endoreg_reference@1.0.0 and a compatible backend release; standard load_base_db_data cannot repair legacy relationships.
- Both startup paths reuse an identical catalogue receipt when all bound rows exist, without comparing fields or relationships. Add --verify for full reconciliation; reuse scope remains in [DataLoading.yml](../../feature-tracking/DataLoading.yml).
- Add --dry-run to preview without hydration or database changes; the registry must already be provisioned.

### Preparation

- Install a reviewed endoreg-db artifact containing the command and packaged migration specification.
- Use the target service's prepared Python runtime, Django settings and protected environment.
- Retain the approved backup and prevent non-cooperating catalogue writers during maintenance.
- The command uses the registered package; it never provisions or silently replaces the registry.

### Preview

| Field | Value |
| --- | --- |
| `command` | python manage.py migrate_reference_catalog --dry-run |
| `optional` | true |

#### Behavior

- Prints proposed dependency creation, before/after relationships and blockers without changing database rows or registry state.
- No review file, reviewer argument or separate approval step is required by the command.
- Exits unsuccessfully when differences fall outside the pinned migration scope.

### Apply

**command:** python manage.py migrate_reference_catalog

#### Behavior

- Checks the pinned catalogue content under the existing catalogue writer lock and PostgreSQL advisory lock; an identical receipt with all bound rows returns unchanged unless --verify is given.
- Otherwise compares current database state before applying the following steps.
- Locks existing catalogue rows and recomputes the plan before changing relationships.
- Creates the missing dependencies through the existing importer and updates only the seven specified relationships.
- Imports the complete catalogue and reruns strict reconciliation before committing one database transaction.
- Any dependency, relationship, receipt or reconciliation failure rolls back all database changes.
- Repeating the command after successful reconciliation is a no-op.
- Emits reference_catalog.migrated after commit with the migration identity, catalogue digest and exact changes.

### Verify

| Field | Value |
| --- | --- |
| `command` | python manage.py import_reference_catalog --module endoreg_reference --module-version 1.0.0 --adopt-existing --dry-run |
| `expected` | Command succeeds, conflicts are empty, all rows are reused, and unchanged is true. |

### Startup

- Only after the migration and reconciliation succeed, rerun the authorized base-data/startup chain on gc-02.
- Check the lx-annotate journal namespace, base-data unit, web unit and acceptance checks.
- No restart, database mutation or production approval is implied by local tests.

## Failure handling

- Additional live relationship differences are checked by the command and remain unknown until it runs against the target database.
- When relationship repair is needed, unexpected removals, generic-intervention changes, scalar-field differences or missing unrelated records remain blockers.
- Existing imported indications are immutable; this legacy-only migration cannot repair drift under prior receipts.
- Do not add force-overwrite behavior or repeat adopt-existing against unresolved differences.
- Preserve command output and commit logs. Database rollback after a committed clinical migration requires the approved backup/recovery procedure.
