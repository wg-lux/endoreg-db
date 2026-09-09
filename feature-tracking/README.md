# Feature Tracking for Production Readiness

This directory is the authoritative readiness record for major `endoreg_db`
features. Readiness is derived from required criteria in each YAML file. A
percentage shows progress but is not approval. A feature is production ready
only when every required criterion is `verified` with traceable evidence.

## Quick start

Run commands from the repository root:

```bash
./feature-tracking/tracker.py
./feature-tracking/tracker.py show dicom
./feature-tracking/tracker.py validate
./feature-tracking/tracker.py check dicom fhir
./feature-tracking/tracker.py overview --all
```

The no-argument overview marks structurally and policy-valid features as
evaluated; criteria and score still show actual verification. `check` exits `1`
while a selected feature is not ready. Invalid YAML, policy violations, or
unsafe commands exit `2`.

## Feature locks and messages

Before changing a file, acquire a time-limited lock. Omitting `--criterion` and
`--file` locks the whole feature; narrower scopes allow independent work.

```bash
./feature-tracking/tracker.py lock acquire standard \
  --criterion terminal_commands \
  --file feature-tracking/tracker.py \
  --owner "codex/session-42" --ttl-minutes 240
./feature-tracking/tracker.py lock status standard
./feature-tracking/tracker.py lock renew <lock_id> \
  --owner "codex/session-42" --ttl-minutes 240
./feature-tracking/tracker.py lock release <lock_id> --owner "codex/session-42"
```

Feature-wide locks conflict with every lock for that feature. Identical
criteria and overlapping files also conflict, including across features. The
default lifetime is four hours and the maximum is 24 hours. Expired locks are
removed by the next lock operation. Runtime locks are not versioned.

Use the same stable owner identifier for owner-private messages. Read the inbox
before changes; lock acquisition also shows unread messages.

```bash
./feature-tracking/tracker.py message inbox --owner "codex/session-42"
./feature-tracking/tracker.py message send \
  --from "codex/manager" --to "codex/session-42" --severity blocking \
  --subject "Correct evidence" --body "Run tracker.py validate." \
  --feature standard --criterion terminal_commands
./feature-tracking/tracker.py message ack <message_id> --owner "codex/session-42"
./feature-tracking/tracker.py message reply <message_id> \
  --from "codex/session-42" --body "Corrected; validation passes."
```

Messages expire, reject terminal control characters, and are operational
coordination only. Never include secrets, patient data, or complete sensitive
payloads. Feature YAML remains the sole readiness source.

## Typed multi-agent orchestration

Use multiple workers only for independently executable branches. A strict JSON
contract selects `single_agent` or `centralized_multi_agent`, names one
orchestrator, limits workers to four and turns to one or two, and caps the total
token budget at 50,000. Each work unit has one responsibility and returns a
schema-valid result with `task_status`, evidenced `findings`, confidence, and
explicit `gaps`. Workers report to the orchestrator, not to a peer mesh.

Centralized plans select `native_subagent` or `external_codex_exec`. Native
subagents inherit the parent permission boundary. External workers permit only
`read-only` or `workspace-write` with approval policy `never`. Both backends use
the same locks, owners, structured results, budgets, and checkpoints.

```bash
./feature-tracking/tracker.py orchestration validate run-contract.json
./feature-tracking/tracker.py orchestration checkpoint run-contract.json audit_api \
  --status in_progress
./feature-tracking/tracker.py orchestration checkpoint run-contract.json audit_api \
  --status complete --result-file audit-api-result.json
```

Valid transitions are `pending` to `in_progress`, then `complete` or `blocked`.
Transitions are atomic and idempotent; invalid transitions fail loudly.

## Commit gate

The `commit-msg` hook requires every criterion of a feature named in the commit
message to be verified in staged YAML. Feature names are normalized for case,
underscores, hyphens, and spaces; template comments are ignored.

```bash
pre-commit install --hook-type pre-commit --hook-type commit-msg
./feature-tracking/tracker.py guard-commit-message .git/COMMIT_EDITMSG
```

The guard reads the Git index, so unstaged assessments cannot bypass it.

## Assessments, completion, and evidence

```bash
./feature-tracking/tracker.py update dicom security_controls \
  --status verified --assessed-by "name@example.org" \
  --acceptance-bullet 1 --acceptance-bullet 2 \
  --note "Both acceptance points are satisfied; no required work remains." \
  --evidence review "security-review-2026-07-17" \
  --evidence test "tests/services/test_dicom_interoperability.py"
./feature-tracking/tracker.py verify dicom automated_tests \
  --update --assessed-by "name@example.org"
./feature-tracking/tracker.py done dicom \
  --assessed-by "name@example.org" --note "Production approval 2026-07"
./feature-tracking/tracker.py reopen dicom documented_scope \
  --assessed-by "name@example.org" --note "A new DICOM profile is supported"
```

Review every acceptance bullet against evidence before `verified`. Assessment
notes must describe the fulfilled state; `validate` rejects outstanding work in
a verified criterion. `verify --update` records successful commands as
`in_progress` and failed commands as `blocked`; only review can raise the status
to `verified`. Updates use atomic, structured-logging file-operation wrappers.

`done` records assessor, time, and rationale and moves YAML atomically into
`feature-tracking/done/`. Reopening moves it back and sets the named criterion
to `in_progress`. Completed assessments are otherwise immutable.

Every feature has a stable identifier, name, description, owners, required
categories, concrete acceptance statements, one verification method per
criterion, and an assessment. Allowed states are `not_assessed`, `in_progress`,
`blocked`, and `verified`. Optional criteria must explicitly use
`required: false`. Clinical and security criteria have no implicit exemption.

Evidence is a durable, reviewable test, command, review identifier, runbook,
dashboard, approved document, or reproducible demonstration. “Works locally”
and percentages are insufficient. Commands are argument lists executed without
a shell. Multi-repository checks use ordered commands with absolute working
directories. A criterion may define `command` or `commands`, never both.

## Maintenance workflow

1. Sharpen acceptance criteria before or with implementation.
2. Implement and test.
3. Run the verification or complete the manual review.
4. Update the assessment with durable evidence.
5. Run `validate`, then `check <feature>`.
6. Review definition, assessment, and evidence changes together.

`policy.yml` inventories migrated Markdown plans and TODOs. Each maps through
`source_documents` to one feature with `disposition: migrated`. Old documents
may remain as history or domain context, but binding criteria, assessments, and
evidence live only in YAML. AGENTS.md prohibits parallel Markdown trackers.
