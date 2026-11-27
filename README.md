[![Built with devenv](https://devenv.sh/assets/devenv-badge.svg)](https://devenv.sh)

# EndoReg-DB

EndoReg-DB helps clinical teams manage endoscopy data and prepare it for research.
It is a reusable Django application with shared data models, terminology, report
and video processing, and authenticated APIs.

Start with the [project overview](docs/project_overview.md). Then follow the
[contributor roadmap](docs/README.md#contributor-roadmap) to explore the system,
set it up locally, and choose a contribution.

## Quick start

This checkout uses Python 3.12, `uv`, and the environment in `devenv.nix`.
From the repository root:

```bash
direnv allow
devenv tasks run agent:sync
uv run python manage.py migrate
uv run python manage.py setup_endoreg_db
```

Shell entry and reload activate the existing virtual environment without creating
or syncing it. Run `agent:sync` explicitly for initial setup or dependency changes,
when no tests or workers are using that environment. Nix supplies the pinned report
embedding model data and its path for the integration tests.

Git hooks use `prek`: run `prek install -f` inside the development shell to
replace both configured hooks (`pre-commit` and `commit-msg`), including legacy
Python pre-commit shims. Then run `devenv tasks run agent:pre-commit` for the full
default suite. Devenv sets its cache to `.devenv/state/prek`; reload with `direnv reload`
after changing the shell configuration, including if an old shell exported an
empty `PREK_HOME`. `devenv tasks run agent:format` runs the configured formatter.

`setup_endoreg_db` loads base data and runs setup checks. To load base data
separately, use `load_base_db_data` as described in the
[configuration guide](setup/CONFIGURATION_GUIDE.md). That guide also covers
environment variables, deployment roles, and protected storage.

## Explore and contribute

| Your goal | Start here |
| --- | --- |
| Understand the project | [Capabilities and deployment roles](docs/project_overview.md) |
| Make a first contribution | [Contributor roadmap](docs/README.md#contributor-roadmap) |
| Explore development areas | [Project development](docs/README.md#project-development) |
| Integrate an application | [API, access, and security](docs/README.md#api-access-and-security) |
| Run a deployment | [Deployment and operations](docs/README.md#deployment-and-operations) |
| Try the data models | [Django shell examples](docs/developer_examples.md) |
| Check feature progress | [Feature tracker](feature-tracking/README.md) |

## Development

Run the fast test lane:

```bash
devenv tasks run test:fast
```

Read [AGENTS.md](AGENTS.md) for contribution rules and required checks. The
[documentation index](docs/README.md) links to guides, contracts, and runbooks.

## Data handling

Local watchers and authenticated APIs use shared import services. Applications
access media through center-scoped APIs. Deployments provide encrypted storage
as required by their storage profile.

Read the [privacy guide](setup/HOW_WE_KEEP_DATA_PRIVATE.md) and
[video storage contract](docs/video_storage_normalization.md) before changing
these workflows. See [backup and recovery](docs/backup_recovery.md) for recovery
procedures.

## Repository map

| Path | Contents |
| --- | --- |
| `endoreg_db/` | Application, services, models, commands, and seed data |
| `docs/` | Architecture, APIs, guides, and runbooks |
| `setup/` | Configuration, privacy, and AI setup |
| `feature-tracking/` | Feature scope, acceptance criteria, and verification evidence |
| `tests/` | Automated tests grouped by application area |
| `scripts/` | Repository tools and checks; GPU diagnostics are in `diagnostics/` |
| `workflow/` | Reusable workflow profiles |

Further reading: [AI model setup](setup/AI_MODEL_SETUP.md),
[companion tools](docs/companion_tools.md), and [license](LICENSE).
