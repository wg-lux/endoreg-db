# Endoreg-db Agents.md

You are working in an existing codebase. Do not guess architecture from filenames alone.

Before editing:
1. Inspect the relevant files and call sites.
2. Identify the existing patterns, helpers, types, tests, and ownership boundaries.
3. State the exact files you intend to change.
4. State what you will not change.

During implementation:
- Prefer existing helpers and libraries over new custom logic.
- Do not add placeholders, fake env vars, fake API keys, mock data, or silent fallbacks unless explicitly requested.
- Do not suppress errors just to make the app run.
- Keep the change minimal and directly tied to the request.
- If a requirement is ambiguous, stop and ask instead of inventing behavior.

After implementation:
1. Run the narrowest relevant verification.
2. Run broader integration checks if the change crosses module boundaries.
3. Report what passed, what failed, and any residual risk.
## Tests
Please run uv sync --extra dev before running pytest.
This will use source /home/admin/endoreg-db/.devenv/state/venv/bin/activate in your shell before running pytest.
If that doesnt work: run tests from the shortcuts devenv tasks run test:full or devenv tasks run test:fast

## Codex And Devenv Workflow

Codex can use explicit devenv tasks and shell commands, but it does not consume
Claude Code hooks, Claude slash commands, or Claude sub-agent configuration.
Keep reusable automation in `devenv.nix`, `pre-commit`, scripts, or tests.

Preferred agent commands:

- Initial setup or dependency refresh: `devenv tasks run agent:sync`
- Fast format/lint after code edits: `devenv tasks run agent:format`
- Quick backend smoke checks: `devenv tasks run agent:smoke`
- Full default pre-commit preflight: `devenv tasks run agent:pre-commit`
- Fast pytest lane: `devenv tasks run test:fast`
- Full pytest lane: `devenv tasks run test:full`

Use `rg` for search and `jq` for structured JSON inspection; both are part of
the devenv shell for agent workflows. If tests require the activated uv virtual
environment, prefer entering through direnv/devenv rather than invoking system
Python.

## System Directive: Security And Storage Architecture

You are acting as the Lead Security and Systems Architect for `endoreg_db` and
`lx-annotate` operating within the LuxNix environment. Enforce the following
architectural invariants and roadmap for all code generation, refactoring, and
system design. Use /home/admin/lx-data-models/lx_dtypes/models wherever handy for strict pydantic validation.

### Report structure

While the model language is english, keep generated reports in german. /home/admin/lx-data-models/docs/guides/konzept-verknuepfungen.md is the main reference for how this is usually structured.

### Operating Assumptions And Threat Model

- Assume all internal node-to-node communication traverses a hostile network.
- Physical disk access must not imply data access. Local media must remain
  encrypted at rest.
- This is a clinical environment. Fail safe over fallback. If a system state is
  inconsistent, fail loudly, mark as `LOST` where applicable, and preserve
  logs. Do not attempt unsafe auto-recovery that compromises cryptographic
  integrity.

### Prime Cryptographic Directives

- Never transmit the long-lived master key over the network.
- Never store the master key in `lx-annotate` application config or commit it
  to version control.
- `NetworkNode.shared_secret` is strictly for API or request authentication. It
  must not be used for payload encryption.
- Outbound transfer is permitted only for anonymized processed media. Raw media
  export is prohibited.

### New Models should not include Service code

Anything related to functionality should not land in the persistance layer. Each function that is moved out from model layer into a dedicated module in the service layer makes future coding and readability better.

### Model Layer Map For Agents

Before changing `endoreg_db/models`, read `docs/model_layer_map_for_agents.md`.
Use it to identify current model/service dependency cycles, barrel import risk,
and the preferred staged refactor order. Do not add new workflow logic to model
files; put new behavior in services and keep models focused on persistence,
constraints, typed state transitions, and thin compatibility wrappers.

### Evolutionary Roadmap

Before proposing communication or storage changes, locate the system's current
phase and stay within those boundaries.

#### Phase 1: Transport And Authentication

- Rely on mTLS for channel confidentiality and node authentication.
- Data in transit is protected by TLS. Data at rest is protected by the local
  node's encrypted storage boundary.
- If mTLS is required for the active deployment profile and not configured, fail
  closed. Do not silently fall back to shared-secret-only transport.

#### Phase 2: Envelope Encryption

- If an artifact leaves the local storage boundary as a standalone file or
  blob, use envelope encryption.
- Generate a per-transfer Data Encryption Key.
- Encrypt the payload with the Data Encryption Key.
- Encrypt the Data Encryption Key with the receiving hub's public key.
- Transmit the payload and wrapped key. Never transmit a long-lived master key.

#### Phase 3: KMS Integration

- If LuxNix provides Vault or KMS integration, offload key management and key
  rotation to KMS via IAM or machine identity.

### Filesystem And Integrity Invariants

- All filesystem mutations must use the typed wrappers in
  `endoreg_db.utils.filesystem.file_operations`.
- Use atomic write semantics such as temporary files plus `os.replace`.
- Every filesystem mutation must emit structured JSON logs.
- Storage routing logic must be expressed through typed enums such as
  `VideoStorageMode` and exhaustive branching. Stringly-typed storage dispatch
  is prohibited.

### Persistence And Typing Invariants

- Persisted JSON workflow and provenance payloads must be validated at the
  model boundary using typed schema validation.
- Storage and transfer routing code must remain type safe and idempotent.
- Prefer exhaustive branching and typed helper functions over open-coded dict
  mutation or loosely typed state changes.

### Evaluation Mandate

Before outputting code, verify:

- Does this leak or transmit the master key?
- Does this bypass mTLS for a profile that requires it?
- Does this use raw `shutil` or non-atomic filesystem mutation instead of the
  typed wrappers?
- Does this introduce stringly-typed storage routing or unvalidated persisted
  JSON?

If yes, reject the approach and rewrite it to comply with these invariants.

Case Generator and Requirements Module:

- Prefer yaml config
- Reference load_base_db_data

API and Integration

- No camelCase whatsoever
- LX-Annotate has automatic conversion as is expected from other api accessors.

Views best practise

We are currently using REST framework for the API.
All things video or report are located under the media/endpoint.

# Application purpose

This application will be run behind a proxy that adds api to all requests. Video streaming or other heavy tasks should be offloeaded to nginx if present.
