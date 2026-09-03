# Endoreg-db Agents.md

The goal of this repository is to provide consistent, anonymous data and to keep it encrypted and safe. Import is handled by video_import_service.py or report_import_service.py.

1. Ensure Import Always Works After Editing
2. Ensure Data Is Streamable
3. Ensure Concurrent Behaviour Is Given

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

Run tests intentionally from one foreground shell command so the process tree is
auditable and easy to stop.

- For focused verification, use the project venv directly:
  `/home/admin/endoreg-db/.devenv/state/venv/bin/pytest <path-or-nodeid>`.
- For broader lanes, prefer the repository tasks:
  `devenv tasks run test:fast` or `devenv tasks run test:full`.
- For code changes, run `/home/admin/endoreg-db/.devenv/state/venv/bin/pyright`
  before pytest.
- Do not start tests through editor coverage/test commands or extensions. In
  particular, avoid VS Code coverage commands that launch `py.test --cov=.` in
  the background; they can create many long-running coverage suites outside the
  agent-visible shell.
- Before starting a broad lane, check for existing pytest processes with
  `pgrep -af 'pytest|py.test'` and report any unrelated running suites instead
  of stacking another full run.

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

## Feature Readiness Tracking

`feature-tracking/` is the single source of truth for feature scope,
Definition of Done, implementation maturity, and production-readiness evidence.
Do not create or maintain parallel TODO, roadmap, implementation-status, or
completion-tracking Markdown files.

Before changing a tracked feature:

1. Read its YAML definition in `feature-tracking/` and `policy.yml`.
2. Acquire a feature lock before the first file edit with
   `./feature-tracking/tracker.py lock acquire <feature_id> --owner <agent_id>`.
   Prefer `--criterion <criterion_id>` and one or more `--file <repo_path>`
   arguments when independent work can safely proceed in parallel. If the
   command reports a conflict, do not edit the overlapping scope; inspect it
   with `tracker.py lock status` and coordinate with the recorded owner.
   Use the same stable `<agent_id>` for the entire Codex CLI process. Before
   editing, read messages with
   `./feature-tracking/tracker.py message inbox --owner <agent_id>`; lock
   acquisition also prints unread messages. Acknowledge acted-on feedback with
   `tracker.py message ack <message_id> --owner <agent_id>` and reply when the
   manager needs a decision or verification result.
3. Add or sharpen measurable acceptance criteria before implementing scope that
   is not represented yet.
4. Do not declare a criterion `verified` without stable evidence and an
   identified assessor.

Renew the lock before it expires and release it with
`tracker.py lock release <lock_id> --owner <agent_id>` when the work ends,
including after a failed implementation attempt. The bootstrap change that
first introduces the lock command is the only exception to acquisition.

After changing a tracked feature, run:

- `./feature-tracking/tracker.py validate`
- `./feature-tracking/tracker.py show <feature_id>`
- `./feature-tracking/tracker.py check <feature_id>` when assessing production
  readiness

Use `tracker.py update` or `tracker.py verify --update` for status changes so
the YAML remains schema-valid and writes are atomic. Markdown documents may
remain as architecture, design, or operational references, but they must point
to the corresponding feature YAML and must not carry an independent completion
status.

Agent messages are local operational coordination only. They may link to a
feature, criterion, or file, but must not contain secrets, patient data, or an
independent implementation/readiness status. Feature YAML remains authoritative.

Use `rg` for search and `jq` for structured JSON inspection; both are part of
the devenv shell for agent workflows. If tests require the activated uv virtual
environment, prefer entering through direnv/devenv rather than invoking system
Python.

## LLM Programming Style Guide

Use types as a primary safety rail. This project uses strict Pyright. For code changes, run Pyright before pytest and treat type failures as
implementation failures, not cleanup. If a proposed diagnosis would imply a
type error, ask whether the types should have caught it and tighten the type
boundary where appropriate.

Type expectations:

- Wherever possible, typed files should live in lx_dtypes and use the existing knowledge_base.
- Prefer explicit function signatures, return types, typed dataclasses, enums,
  `TypedDict`, and Pydantic models over unstructured dictionaries.
- Annotate class attributes in the class body when they are assigned later.
- Avoid broadening types to make a failing test pass. Optional and union types
  need a concrete domain reason.
- Avoid `Any`. When interfacing with framework or external-library dynamic
  data, validate or narrow it at the boundary and pass typed objects inward.
- Use overloads or literal-discriminated helpers when inputs determine return
  types.

Boundary and invariant rules:

- Convert external input at the edge: request payloads, files, YAML/JSON,
  environment variables, command options, and third-party API responses should
  be normalized once and then represented with one typed internal shape.
- Define valid input invariants for non-trivial functions. Invalid input must
  raise loudly rather than being silently ignored.
- Prefer pure functions and returned values for transformation logic. Keep
  database writes, filesystem writes, network calls, and object mutation at
  explicit workflow boundaries.

Exception handling:

- Avoid broad `except Exception` outside request, command, job, or integration
  boundaries.
- Keep `try` blocks narrow, usually around one operation, and catch specific
  exception classes.
- Do not add silent fallbacks. If fallback behavior is explicitly required,
  make it named, logged, tested, and safe for clinical/security invariants.

Testing expectations:

- Add parametrized tests for meaningful valid input variation.
- Add invalid-input tests for invariants and boundary validation.
- Prefer focused unit tests for pure logic and integration tests only where
  contracts cross services, persistence, filesystem, or API boundaries.
- For code changes, run `.devenv/state/venv/bin/pyright` before pytest.
- Do not use one-off scripts as a substitute for reusable tests when the
  behavior is important.

## System Directive: Security And Storage Architecture

You are acting as the Lead Security and Systems Architect for `endoreg_db` and
`lx-annotate` operating within the LuxNix environment. Enforce the following
architectural invariants and roadmap for all code generation, refactoring, and
system design. Use /home/admin/lx-data-models/lx_dtypes/models wherever handy for strict pydantic validation.

## Mandatory Video Rules For All Agents

These rules apply to every change involving video import, reimport,
reanonymization, transcoding, storage, HTTP Live Streaming (HLS), frame
extraction, timeline or segment coordinates, cleanup, migration, or export.
Before changing any of these paths, read:

- `docs/video_storage_normalization.md`, the canonical English operational and
  architecture runbook;
- `feature-tracking/VideoStorageNormalization.yml`, the only source of truth
  for scope, approval state, and production-readiness evidence;
- `docs/video_pts_fps_callsite_inventory.md` when frames per second (FPS),
  presentation timestamps (PTS), frame indices, seeking, or segment boundaries
  are involved.

Do not use unexplained abbreviations in video code, documentation, logs,
user-facing text, or feature-tracker evidence. Spell out a term at its first
use and add durable video terminology to the runbook glossary.

All agents must preserve these video invariants:

- Exactly one canonical anonymized master generation is published. Raw media,
  streamable MPEG-4 Part 14 (MP4), HLS, extracted frames, and transcode staging
  files have distinct lifecycle roles and must not be treated as
  interchangeable masters.
- The versioned typed storage profile is mandatory. Media outside its
  resolution, frame-rate, bitrate, byte-budget, codec, pixel-format, duration,
  or timeline limits must fail loudly or enter an explicit quarantine process.
  Stream copy, unbounded source-quality encoding, and upsampling are not safe
  fallbacks.
- Persisted presentation timestamps are authoritative for clinical segment and
  frame identity. For variable-frame-rate (VFR) media, nominal frames per
  second alone is never sufficient. Do not rewrite frame coordinates after
  segment rows or extracted frames exist.
- Storage normalization preserves the source timeline. The separate
  `annotation_fps_resample_v1` workflow may convert videos above 50 frames per
  second to exactly 50 frames per second only before the first segment or
  extracted-frame coordinate is persisted.
- Playlist, key, and segment access renews a media-operation lease. Transcoding,
  HLS regeneration, generation replacement, and cleanup must defer while a
  playback or segment-update lease is active and must publish one generation
  atomically.
- All video staging and publication stays inside the approved encrypted storage
  boundary. Every filesystem mutation uses
  `endoreg_db.utils.filesystem.file_operations`, atomic semantics, and
  structured JavaScript Object Notation (JSON) logging. Raw media export is
  prohibited.
- Cleanup is fail-closed. Never delete the previous or only valid master before
  target validation, hash and timeline checks, clinical-quality approval, HLS
  generation matching, lease expiry, and database/filesystem reconciliation.
- Destructive legacy migration remains disabled until the temporal and clinical
  quality gates are verified and the required operations, storage, security,
  and clinical approvals are recorded through the feature tracker.

The canonical video runbook is maintained in English so it can be reviewed by
all participating teams; this is an explicit exception to the general German
report-language convention below.

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
