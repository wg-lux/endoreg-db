# Hierarchical agent context

The repository root [structure.yml](../structure.yml) routes agents to bounded
directory maps. This navigation contract belongs to
[CodeQuality.yml](../feature-tracking/CodeQuality.yml), criterion
`hierarchical_agent_context`. Feature definitions retain all readiness evidence.

Read the root map, follow the relevant child link, then inspect actual entrypoints
and required contracts. Load sibling domains when dependencies or callers matter.
Scoped searches remain necessary. When maps disagree with code, investigate and
repair them; do not infer implementation from summaries.

The [AGENTS.md protocol](../AGENTS.md#source-of-truth-hierarchy--agent-context-protocol)
requires every modifying agent to maintain affected maps. Delegation includes
the nearest map, editable paths, acceptance criteria, commands, and concrete results.
Keep durable ownership knowledge in maps and operational messages in the tracker.

## Version 1 schema

Every manifest requires `version: 1`, `tier` (`root`, `domain`, `service`, `module`),
`path`, `parent`, `summary`, nonempty `responsibilities`, and nonempty `boundaries`.
The root has `path: .`, `tier: root`, and `parent: null`. Other manifests link back
to the manifest that routes to them.

| Field | Meaning and path base |
| --- | --- |
| `path` | Repository-relative containing directory |
| `parent` | Manifest-relative parent manifest |
| `entrypoints` | Objects with `name`, `file`, `purpose`; file is manifest-relative |
| `files` | Objects with `file`, `role`; file is manifest-relative |
| `submodules` | Objects with `name`, `path`, `manifest`, `summary`; paths are manifest-relative strict descendant directories |
| `dependencies.internal` | Repository-relative package directories or module files |
| `dependencies.external` | Key libraries or protocols, without local path validation |
| `contracts` | Objects with `name`, `doc`; doc is a repository-relative file |
| `tests` | `directory`, `fast_lane_command`, optional `files`; paths are repository-relative; commands run from repository root |

Optional collections default to empty; `tests` may be omitted without a direct
lane. Absolute paths and references escaping the repository, including symlinks,
are rejected. Existing service `domains`, `module_moves`, and hub `modules` remain
compatibility metadata. The common schema validates navigation fields without
replacing those ownership records.

Example child route from `endoreg_db/services/structure.yml`:

```yaml
submodules:
  - name: hub
    path: hub
    manifest: hub/structure.yml
    summary: Hub ingestion and authenticated node transfer.
```

## Verification and limits

Run `.devenv/state/venv/bin/python scripts/lint_structure.py`; the local pre-commit
hook also runs it. Git discovers tracked and unignored new project maps without
traversing ignored media, environments, or caches. The validator checks typed
fields, references, reciprocal parents, duplicate ownership, descendant scope,
and root reachability. It performs no writes and never executes manifest commands.

Review summaries, symbols, ownership, dependencies, and command relevance separately.
Validation checks references, not every source file's coverage or architectural
truth. List newly created files locally; maps need not enumerate every historical
source file. Add hierarchy only at useful ownership boundaries.

## Public guidance

Guidance reviewed on 2026-10-13:

- The open [AGENTS.md specification](https://agents.md/) recommends actionable
  repository instructions and nested files for localized rules. Here global rules
  stay in AGENTS.md and agents explicitly read YAML for navigation.
- [Effective context engineering for agents](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)
  describes retrieving relevant context as needed through lightweight identifiers
  and progressive disclosure. Here that means paths, bounded summaries, and links
  instead of loading the repository at once.
- The July 2026 study [Is Progressive Disclosure All You Need for Long-Context Agents?](https://arxiv.org/abs/2607.17598)
  finds that benefits depend on navigation capabilities and that extra routing
  depth can reduce accuracy in its long-document benchmark. This is not direct
  evidence about this repository; it motivates shallow maps unless actual
  ownership boundaries justify another level.

These public references explain design choices; no commercial product or service
is required for discovery, maintenance, or validation.
