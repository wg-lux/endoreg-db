# Reference provisioning layers

Scope and verification belong to
[DtypesStudyDefinitions.yml](../../feature-tracking/DtypesStudyDefinitions.yml).
Operational authoring and migration commands are documented in the
[YAML authoring guide](dataloader_yaml_authoring.md).

- `setup_deployment` composes explicit site provisioning, versioned catalogue
  imports and optional registry knowledge-base selection from the shared
  `DeploymentSetup` contract. Its reviewed lock pins resolved content. Database
  provisioning and subsequent registry activation have separate recovery
  boundaries; see the operator guide.
- `lx_dtypes.models.interface.DataLoader` resolves standard knowledge-base YAML
  and validates shared contracts. Registry provisioning owns filesystem sources.
- `endoreg_db.services.reference_data.catalog` reconciles typed reference snapshots
  and projects them atomically into explicitly allowed ORM models.
- `endoreg_db.services.reference_data.clinical_projection` projects the canonical clinical
  graph with its complete immutable definition snapshot.
- `endoreg_db.management.reference_catalog_command` preserves public load-command
  names with explicit, versioned package selections and dependency closure.
- `endoreg_db.helpers.data_load_orchestrator` exposes command orchestration;
  `helpers.data_loader` remains its compatibility implementation.

The generic `utils.dataloader` engine and `utils.yaml_model_loader` facade are
removed. Their raw fixture API is intentionally discontinued; external callers
must use the management commands or typed services. The independent resolver in
lx-dtypes and test command helpers are not legacy engine implementations.

```python
from endoreg_db.helpers.data_load_orchestrator import load_base_db_data
```
