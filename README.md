[![Built with devenv](https://devenv.sh/assets/devenv-badge.svg)](https://devenv.sh)

# EndoReg-DB

EndoReg-DB is a reusable Django application for clinical and research data. It
provides shared data models, terminology, report and video processing, and
authenticated APIs for ingesting and reading clinical media.

See the [project overview](docs/project_overview.md) for capabilities,
deployment roles, and the repository map.

## Find your way

- **New contributor:** start with the [setup guide](setup/CONFIGURATION_GUIDE.md)
  and [documentation index](docs/README.md).
- **Developer:** use the [devenv workflow](#development) and read the
  [model layer map](docs/model_layer_map_for_agents.md).
- **Deployment operator:** see [deployment and operations](docs/README.md#deployment-and-operations)
  and [configuration](setup/CONFIGURATION_GUIDE.md).
- **API or integration author:** see the [API and security references](docs/README.md#api-access-and-security).
- **Looking for project status:** use the [feature tracker](feature-tracking/README.md).
- **Trying Django shell examples:** see [developer examples](docs/developer_examples.md).
- **Looking for recovery guidance:** see [database backup and recovery](docs/backup_recovery.md).

## Quick start

This checkout uses Python 3.12 and `uv`, configured through `devenv.nix`.
From the repository root:

```bash
direnv allow
devenv tasks run agent:sync
uv run python manage.py migrate
uv run python manage.py setup_endoreg_db
```

`setup_endoreg_db` loads base database data and runs the configured setup
checks. For environment variables, deployment roles, protected storage, and
ingress behavior, see the [configuration guide](setup/CONFIGURATION_GUIDE.md).
Use [`load_base_db_data`](setup/CONFIGURATION_GUIDE.md) when you need to load
only the base seed data.

## Development

Run the repository's documented fast test lane with:

```bash
devenv tasks run test:fast
```

See [AGENTS.md](AGENTS.md) for contribution rules, required checks, and data
handling constraints. The [documentation index](docs/README.md) is the main
catalog for maintained guides, contracts, and runbooks.

## Important boundaries

- Ingest can enter through a trusted local watcher or authenticated API; both
  use the shared processing workflow. See the [hub ingest operations guide](docs/hub_ingest_operations.md).
- Applications should use center-scoped media APIs instead of accessing media
  directories directly. See [API, access, and security](docs/README.md#api-access-and-security).
- Deployments must provide the protected storage boundary required by their
  storage profile. See [configuration](setup/CONFIGURATION_GUIDE.md) and the
  [video storage contract](docs/video_storage_normalization.md).
- Feature scope and readiness evidence belong to
  [`feature-tracking/`](feature-tracking/README.md), not in this overview.

## Repository map

| Path | What it contains |
| --- | --- |
| `endoreg_db/` | Django application, services, models, management commands, and seed data |
| `docs/` | Maintained architecture contracts, API references, guides, and runbooks |
| `setup/` | Configuration, privacy, and AI setup guides |
| `feature-tracking/` | Feature definitions, acceptance criteria, and readiness evidence |
| `tests/` | Automated tests grouped by application area |
| `workflow/` | Reusable workflow profiles |

## Further information

- [Documentation index](docs/README.md)
- [Configuration guide](setup/CONFIGURATION_GUIDE.md)
- [Protecting data](setup/HOW_WE_KEEP_DATA_PRIVATE.md)
- [AI model setup](setup/AI_MODEL_SETUP.md)
- [Developer examples](docs/developer_examples.md)
- [Database backup and recovery](docs/backup_recovery.md)
- [Optional companion tools](docs/companion_tools.md)
- [Historical Wiki references](docs/legacy_wiki_index.md)
- [Feature tracker](feature-tracking/README.md)
- [License](LICENSE)
