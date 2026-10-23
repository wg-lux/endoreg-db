# Approach A: private comparison of existing patient HMACs

Assessment of October 19, 2026. Scope and approval status are maintained exclusively
in [HubTransfer.yml](../feature-tracking/HubTransfer.yml),
`existing_hmac_2pc_evaluation` and `cross_site_private_patient_matching`.
This is an isolated evaluation, not operational approval or a migration.

## A. Decision

| Claim | Decision | Rationale |
|---|---|---|
| Cryptographic computability | **supported** | HMAC-SHA256 and equality are finite Boolean circuits. An actual two-process evaluation using MP-SPDZ/Tinier was executed. A need not disclose its key to B. |
| Exact tuple comparison | **conditionally supported** | Reference tests agree with production encoding. The experimental circuit processes preserialized bytes; it does not enforce that a malicious B supplies valid patient JSON with A as the center. |
| Authenticated relationship between existing UUIDs | **not established by current evidence** | The computation binds neither database rows nor UUIDs nor permissions. Signed matching results and a persisted relationship model are missing. |
| Verified identity of the same real person | **not established by current evidence** | Identical names/birthdays can refer to different people; name changes or spelling variants prevent genuine matches. The verified issuer contract remains unfulfilled. |
| Practical performance | **conditionally supported** | Local synthetic measurements establish only the quantities reported below; no claim about clinical wide-area networks, concurrency, or production capacity. |
| Operational readiness | **not established by current evidence** | Independent access boundaries, authenticated input binding, a minimal result channel, abuse controls, integration, and security review are missing. |

Five claims must be distinguished: (1) a candidate's bytes produce the target
HMAC under the selected key; (2) the target belongs to the claimed source UUID;
(3) the candidate belongs to the claimed B UUID; (4) both concern the same real
person; (5) nobody learns more than the defined protocol leakage. The circuit
examines (1); (2) through (5) do not follow automatically. A signature confirms
a key holder's statement, not the truth of demographics. B recognizes a
deliberately linked source record if it already knows its own patient.

## B. Current findings and evidence

The actual working copies, including preexisting changes, were examined:
`/home/admin/endoreg-db`, branch `prototype`, commit
`08c1ac0da110343dfc6d0314b24998a1937d9aeb`; `/home/admin/luxnix`, branch
`lx-annotate_ollama`, commit `cda962d31bda2811cecdeaeb413c6c84f8a0063e`.
Both working trees were already modified. No reset, checkout, production access,
secret retrieval, or database restructuring occurred. Evidence refers to the
reviewed working tree, not exclusively HEAD. LuxNix source establishes declared
configuration, not deployed state.

Unprefixed paths are in endoreg-db; `luxnix/` denotes the second working copy.
Line numbers are a snapshot and can shift with concurrent edits.

| Claim | Status | Code or deployment evidence | Assumptions | Remaining gap |
|---|---|---|---|---|
| Exact v1 encoding and HMAC | implemented | `endoreg_db/utils/hashs.py:75–118` | Schema really is v1 | Legacy records are not universally v1 |
| Key string remains UTF-8 | implemented | `utils/hashs.py:31–55`, `utils/verify_salt.py:65–110` under `endoreg_db/` | Valid configured material | Strength checking does not prove random generation |
| Public generation fingerprint | implemented | `endoreg_db/utils/hashs.py:70–72` | Expected value authenticated | No issuer proof; weak keys permit offline testing |
| Historical keys available | conditional | `endoreg_db/config/secret_keyring.py:21–57,198–205`; `endoreg_db/services/secret_rotation/historical_resolution.py:53–63` | Correct locally retained generation | No individual epoch numbers in retiring tuple; legacy double SHA and HMAC coexist |
| Retiring keys read-only | not enforced | `endoreg_db/utils/hashs.py:38–42` | Caller follows policy | Primitive permits arbitrary new HMAC evaluation |
| Record/hash binding | local checks | `endoreg_db/models/metadata/sensitive_meta.py:145–157`; `models/administration/person/patient/patient_hash_alias.py:17–54` under `endoreg_db/`; `services/secret_rotation/historical_resolution.py:82–134` | Trusted local persistence | No signed scheme/epoch/UUID statement; some fields empty |
| Center unique | not generally | `endoreg_db/models/administration/center/center.py:29–33,85–89,109–125` | Controlled inventory | Not unique; resolve_identity takes first match; save protection is bypassable at database level |
| B holds original demographics | only some sources | `endoreg_db/models/metadata/sensitive_meta.py:128–135`; `services/privacy/quality_evaluation.py:783–880` under `endoreg_db/` | Retained, authorized original values | Deletion and pseudonymization prevent reconstruction from local HMAC |
| Patient model values are originals | false as a general assumption | `endoreg_db/models/administration/person/patient/patient.py:119–138` | None | Pseudonyms and randomized birthday are not matching inputs |
| Hub can be B | not established with current transfer data | `endoreg_db/services/hub/transfers.py:2151–2182` | Additional authorized source required | Hub creates empty names and no date of birth |
| External ID verified | not established | `endoreg_db/models/administration/person/patient/patient_external_id.py:18–31` | Issuer verification to be added | Origin/value without verification evidence |
| UUID preserved | implemented | `endoreg_db/models/administration/person/patient/patient.py:178–180`; `services/hub/transfers.py:2090–2112` under `endoreg_db/` | Authorized source node | Existing transfer binding is not cross-site matching |
| Source hash remains local | implemented | `endoreg_db/services/privacy/response_boundary.py:14–55` | Unchanged export boundaries | New matching must not create blanket disclosure permission |
| Replay/transport protection exists | for transfer | `endoreg_db/services/hub/transfers.py:535–575,887–908`; `views/media/hub/transfers.py:231–280` under `endoreg_db/` | Active mutual Transport Layer Security (mTLS) profile correct | No matching session or limits on fresh adaptive queries |
| Envelope binds metadata | implemented | `endoreg_db/services/hub/transfer_envelope.py:209–243` | Legitimate senders | No signature over an actual matching result |
| Hub and Vault on gs-02 | declared | `luxnix/ansible/inventory/host_vars/gs-02.yml:84,146` | Configuration deployed | Does not rule out direct A/B matching |
| Identity keys provisioned locally | declared | `luxnix/scripts/provision-identity-salts.py:186–203,233` | File permissions and process isolation effective | Application reads keys; root reads recovery |
| Independent administration | not established | `luxnix/ansible/inventory/hosts.ini:177–183`; `group_vars/hub_enrollment_admin_access.yml:2–5` | Check actual sudo/database/backup permissions | Shared controller SSH identity intended |
| Recovery is a separate trust boundary | no | `luxnix/modules/nixos/services/lx-annotate-local/subservices/lx-annotate-identity-salts.nix:14–17,23,30–44` | None | Two files on the same host; root service |

### Production function and historical selection

A holds `uuid_A`, `t_A`, `K_A`, `c_A`, and provenance evidence. B holds a bounded
list `(uuid_B, first_name, last_name, dob)` from authorized original records.
The exact function is `HMAC-SHA256(K_A, EncodePatient(d_i,c_A)) == t_A`.
B's own key and local HMAC are not needed. Deleted demographics cannot be
recovered from this.

The JSON list contains `patient-identity-v1`, trimmed first and last names, ISO
date, and trimmed source center key. `datetime.date()` takes the existing date
without timezone conversion. Python `strip()` also removes outer Unicode
whitespace; there is no lowercasing, accent folding, or NFC/NFD normalization.
`ensure_ascii=False`, compact separators, and UTF-8 remain unchanged; quotation
marks, backslashes, and control characters follow JSON. Invalid Unicode
surrogates cannot be UTF-8 encoded and must cause failure. Hex- or Base64-like
key strings are **not decoded**.

The “collision-free” wording in `hashs.py:83` is incorrect: unambiguous field
encoding is neither an infinite collision-free digest nor a unique person key.
HMAC handles long keys by hashing first, then block padding; cached inner/outer
states must be protected like keys. [RFC 2104](https://www.rfc-editor.org/rfc/rfc2104.html)

The expected generation fingerprint is
`HMAC-SHA256(K_A,b"endoreg-identity-salt-v1")`. It must come from an authenticated
local record/issuer binding, not the same unauthenticated request. An in-circuit
comparison checks key consistency, but not whether `t_A` belongs to `uuid_A`.
Published fingerprints permit offline tests of guessed weak keys. Legacy hashes
must be excluded before selecting the v1 circuit. Retiring keys need explicit
permitted purposes, expiry, and revocation; comments in the hash helper are
insufficient. Caches must not inject independent, unchecked HMAC states as a
substitute for the bound key.

## C. Protocol design and trust boundaries — not yet integrated

The two endpoints are **A's local matching worker** and **B's local matching
worker**. A hub may relay requests but receives neither `K_A`, `t_A`, nor
candidate tuples. No central shared matching key is required. mTLS authenticates
endpoints and protects channels; it does not replace two-party computation (2PC).
A server receiving both inputs over TLS would not be private two-party
computation. Password hashes, API secrets, and master keys are not repurposed
as asymmetric keys.

| Attacker | Expected protection / boundary |
|---|---|
| Honest-but-curious participant | Only defined outputs and public metadata under the backend model; no other party's private inputs. |
| Malicious A or B | Active protocol security detects cryptographic misconduct or aborts; it does not prevent fabricated, formally valid inputs. Authorization binding is additionally required. |
| Curious/compromised hub | May see only relay metadata and subsequently authorized UUID relationship; network traffic requires authenticated encryption. |
| Root/application/database/backup administrators | Can read local inputs. The same sufficiently privileged operator at both endpoints sees both inputs; 2PC does not remove those rights. |
| Collusion | Hub plus one party holds that party's inputs and permitted results; collusion of both parties removes their mutual confidentiality. Shared administration must not be treated as non-collusion. |
| Later compromise | Retained keys plus target token permit offline candidate checking. Logs, crash dumps, swap, backups, and preprocessing/cache state are within the boundary; no blanket forward-secrecy promise. |

Vault does not independently protect against the host processing its live secrets;
the documented model distinguishes untrusted storage from authorized runtime
and policy access. [Vault security model](https://developer.hashicorp.com/vault/docs/internals/security)
The LuxNix SSH group alone does not prove root rights: see
`luxnix/docs/vault-hub-machine-enrollment.md:1063–1066`. Actual root, application,
database, deployment, backup, and recovery rights at both clinics must be
inventoried. The existing requirement for separately protected keys is retained
and applied to the actual A/B roles.

### Inputs, messages, and bindings

1. A authorizes exactly one existing record and creates a signed, short-lived
   session request: random request ID, A/B nodes and issuers, audience, purpose,
   schema, circuit version/digest, epoch reference, expiry, and authorized source
   UUID. The target token remains in A's private input. The local authorization
   service binds `t_A ↔ uuid_A ↔ c_A ↔ scheme ↔ epoch` to the selected immutable
   record version. A field freely chosen by the worker is not evidence of this.
2. B builds an immutable candidate set from permitted, actually existing originals,
   with local UUIDs and version evidence. A controlled local adapter binds tuple
   and UUID; against a malicious site, this requires an additional trusted issuer
   or attestation boundary, or a reviewed credential mechanism. Fabricated
   candidates must not appear verified.
3. Both check session, certificates, purpose, allowed key generation, quantity
   limit, expiry, and identical circuit. They run the reviewed 2PC. Serialization
   outside the circuit is correct for honest adapters; against malicious B, the
   circuit must enforce format and `c_A`, or input must be demonstrably bound to
   a reviewed serializer. This remains open.
4. Minimal domain output: B receives only `no_match`, `single_match` with a local
   match, or `ambiguous_matches`; A initially receives only completion/abort.
   Only after authorized release of a unique match do A and the hub receive the
   necessary UUID relationship. Candidate review for ambiguity remains with B.
   The current experiment instead outputs all match bits to both parties:
   explicitly more leakage than this target, not a finished minimal output.
5. Both parties sign the same result structure, including request ID, participants,
   audience, purpose, schema, epoch, circuit digest, candidate-set reference,
   result, relevant UUIDs, and expiry. Signing keys are separately provisioned
   asymmetric keys. The trusted wrapper may sign only after successful backend
   completion. Two signatures are not a generally publicly verifiable proof of
   the actual computation.
6. The recipient checks signatures, revocation, expiry, session/audience binding,
   and atomic one-time processing. It stores an explicit revocable relationship
   `(issuer_A, uuid_A, issuer_B, uuid_B, evidence, status)` with an audit trail.
   UUIDs and local hashes remain unchanged; there is no automatic merging.

In the target model, only A knows `t_A` and `K_A`, and only B knows the tuples.
Both know session, schema, allowed size classes, publicly agreed key/center
metadata, and connection duration. A publicly visible epoch reference permits
recognition of that epoch; it may be published only with approval. The hub sees
routing, timing, and approved relationships. Size padding can partially hide
candidate count and byte lengths but costs additional circuitry; dummy candidates
must be masked internally. External errors are coarsely classified; sensitive
details remain local.

### Result semantics and repeated queries

| Result | Meaning |
|---|---|
| `no_match` | Complete evaluation of exactly the approved candidate set under the selected scheme/center/key without a match; not global nonexistence. |
| `single_match` | Exactly one matching local record in the complete set, not an automatically verified person. |
| `ambiguous_matches` | Multiple records match, including identical demographics under different UUIDs; no automatic selection. |
| `aborted_or_incomplete` | Timeout, tampering, missing inputs, inconsistent binding, abort, or incomplete set; never a nonmatch. |
| `key_epoch_unavailable` | Selected permitted generation missing; no fallback to active/foreign generation. |

Replay protection does not prevent membership tests with new request IDs,
overlapping batches, or adaptively selected source UUIDs. Query budgets across
patients, purposes, and sites, reviewed candidate sets, repeat consistency,
auditing, and domain access control are needed. An unrestricted authorized
membership oracle remains an identification risk. An aborted run must not
publish usable partial results.

## Implementation comparison: at most two backends

**MP-SPDZ v0.4.4**, commit `113c4e47dd94bca9ecab029d2f0abe649bea5c52`,
is the executed candidate. Boolean SHA-256 circuits permit HMAC composition and
equality; private bits belong to one party each. Tinier is classified as
malicious/dishonest-majority; with two parties, one corrupt party is covered,
without fairness or guaranteed output. The software describes itself as a
benchmarking framework without an assured production security audit.
[Documentation](https://mp-spdz.readthedocs.io/en/latest/readme.html)
The framework description is [Keller 2020](https://eprint.iacr.org/2020/521).

Tinier uses authenticated secret sharing and oblivious transfer (OT)-based
preprocessing according to [FKOS15](https://eprint.iacr.org/2015/901), with the
cut-and-choose adaptation described in the
[pinned source](https://github.com/data61/MP-SPDZ/tree/113c4e47dd94bca9ecab029d2f0abe649bea5c52)
following [Furukawa et al.](https://eprint.iacr.org/2016/944).
OT/symmetric cryptographic assumptions, correct randomness sources, fresh checked
preprocessing, and endpoints not jointly compromised are prerequisites. The
composition of all implementation optimizations with these proofs was not
independently proved or audited here. Message authentication code (MAC) and
protocol failures must abort; domain-invalid but syntactically accepted inputs
remain possible. Fetching the FKOS15 full text through ePrint and the IACR
proceedings archive failed with HTTP 403; the primary abstract, source references,
and implementation were accessible. A complete review of published proof
assumptions is therefore explicitly **not complete**. In particular, the adopted
cut-and-choose analysis is not a blanket proof of a two-party protocol: the cited
Furukawa paper addresses three parties as a complete protocol. BSD-3-Clause for
main code, additional third-party licenses in `License.txt`. Current distribution
and substantial compiler, C++ runtime, OT, OpenSSL, and CPU dependencies imply
significant integration effort; no existing Django adapter or Nix deployment
evidence.

**EMP ag2pc**, commit `a245ca0f67abaf2d48c711e3c050ce961e60ad29`, supports
Boolean circuits, private participant inputs, and recipient-specific output.
The current API describes active security using authenticated garbling,
OT consistency checks before output, and errors on invalid wire lifetime.
SHA-256/HMAC can be constructed from these, but was not executed here. The current
rewrite is explicitly unaudited; Apache-2.0, C++20, CMake, OpenSSL, emp-tool/emp-ot,
and a custom adapter are required.
[Current primary source](https://github.com/emp-toolkit/emp-ag2pc/tree/a245ca0f67abaf2d48c711e3c050ce961e60ad29)
The referenced security analysis is [KRRW18](https://eprint.iacr.org/2018/578);
its authenticated garbling/OT and active-security assumptions do not replace
review of the new code. Here too: two separate parties, connection authentication,
abort instead of reliable availability, and no automatic input authenticity.
No second benchmark or claim of a finished HMAC adapter.

[RFC 9497](https://www.rfc-editor.org/rfc/rfc9497.html) specifies a different
oblivious pseudorandom function (OPRF) / verifiable OPRF (VOPRF). Its evaluation
does not reproduce the existing HMAC; a switch would require a separate design
and migration. Existing bounded candidate search
`scripts/benchmark_sha256.py:20–24,47–99` fixes the key and enumerates messages.
It is neither key recovery nor 2PC; time/attempt limits do not prove exhaustion
outside the declared space.

## D. Revised proof obligations

1. **Correctness:** Differential tests against the production helper, HMAC
   padding, byte/bit order, keys over 64 bytes, and all JSON edge cases; circuit
   binds A's center and permitted hidden input shape or a controlled adapter.
2. **Authorized records:** Evidence for both record/UUID bindings, including
   record version, scheme, issuer, and epoch; malicious participants must not
   present arbitrary target tokens or fabricated candidates as authenticated.
3. **Privacy:** Explicit ideal functionality, corruption model, and leakage
   budget; no claim that known locally linked people remain anonymous to B.
4. **Proof to implementation:** Pin compiler, circuit, runtime, and dependencies;
   review OT, MAC, sacrifice, and output implementations, including selected
   parameters and abort behavior. Functional tests are not a security proof.
5. **Result integrity:** Trusted completion marker, no output before checks;
   dual signatures, audience/purpose/expiry/session binding, atomic replay protection.
6. **Historical keys:** Authenticated epoch fingerprint, actual retiring policy,
   legacy exclusion, revocation, and secure cache/backup deletion; no safety
   claim after key compromise.
7. **Queries and errors:** Batch completeness, ambiguity, no partial results on
   abort, shared budgets even across new request IDs and overlapping sets.
8. **Clinical identity:** Tuple equality distinguished from verified person
   identity; additional issuer-qualified verification and clinical review if needed.
9. **Composition:** mTLS/export boundaries, local encrypted storage, and phase-2
   envelopes for external blobs remain effective. Artifact hashes identify
   files. Shared content digests, metadata, and exported clinical content can
   identify/link independently of matching; 2PC proves no anonymity of the
   complete released dataset.

## E. Smallest justified next step

Another isolated integration experiment with two genuinely separately administered
test endpoints, synthetic data, and an explicit record-binding contract is
justified. Prerequisites: documented application/root/database/backup permissions,
authenticated epoch and record snapshots, reviewed circuit with format binding
and minimal recipient-specific output, pinned backend, and authenticated channel.
No patient merging yet.

Acceptance: both UUID substitutions, foreign/disallowed targets, manipulated
results, expired/replayed sessions, unknown epochs, truncated messages, process
termination, and exceeded cumulative query budgets are rejected as errors
without relationship persistence. Duplicate matching records remain ambiguous.
Signatures bind the same successful session. A production decision additionally
requires security review, a clinical identity decision, and measurement in the
intended network.

## Validation performed

The reference script, its tests and the 2PC circuit named in this section were
removed from the working tree in commit `a965c674`. They remain retrievable at
commit `d2c1adab` (for example `git show d2c1adab:scripts/patient_matching_2pc.mpc`).
The results below are historical evidence for that revision and cannot be rerun
from the current tree.

The isolated reference `scripts/patient_matching_2pc_reference.py` sees both
inputs and claims **no** privacy. The
tests `tests/scripts/test_patient_matching_2pc_reference.py` use only synthetic
names, UUIDs, and keys under isolated test settings. 39 tests pass: production
equivalence, A/B key/center separation, whitespace, case, Unicode/NFC-NFD, JSON
escaping, date/datetime, long and UTF-8 keys, missing/incorrectly bound historical
generation, ambiguous tuples, and incomplete search. An explicit negative
finding shows UUID substitution remains undetected in the reference model.
Signatures, replays, and authorized inputs are **not implemented** there.

Executed in this order before pytest:

```text
.devenv/state/venv/bin/pyright scripts/patient_matching_2pc_reference.py tests/scripts/test_patient_matching_2pc_reference.py
  0 errors, 0 warnings
.devenv/state/venv/bin/pyright
  20 errors, 24 warnings; existing identity_resolve/generate_public_key.py (17)
  and identity_resolve/sha_reverse.py (3), no errors in the new reference files
.devenv/state/venv/bin/pytest tests/scripts/test_patient_matching_2pc_reference.py -q
  39 passed, 1 existing plugin rewrite warning, 0.16s
```

The backend measurements below must be read separately from these reference tests.

### Reproducible synthetic two-process experiment

The persisted circuit `scripts/patient_matching_2pc.mpc` was isolated from
production code. The external laboratory controller under `/tmp` holds both
synthetic inputs; the two MP-SPDZ processes receive separate player inputs.
This demonstrates actual 2PC but not separate administrators: both processes
and input files reside on the same test host. No clinical data, production
keys, new packages in the application virtual environment, or deployment changes.

Reproduction pins:

| Component | Version / SHA-256 |
|---|---|
| [Official release archive](https://github.com/data61/MP-SPDZ/releases/download/v0.4.4/mp-spdz-0.4.4.tar.xz) | v0.4.4; `73d6473526101b4433999fc4f7521fdfdc53ddf018e5f64480518d6293f62760` |
| Bristol circuit repository | `mkskeller/bristol-fashion`, commit `1603ed6aa12348c2ef15ed8395b9b96e4c558b7f` |
| `sha256.txt` | `bd0a91bb7e97bb60c1468fe8caecc546af3f832bd4152d9c8c4e7527412dd11d` |
| `bin/Linux-adx/tinier-party.x` | `d6c168f5c35c9c9c5fda31d03e930bcf2f883601824f5128f0ab4d85343f06c8` |

The initial source build failed because `sodium.h` was missing; the official
Linux binary was executable. `tiny-party.x` aborted during OT setup at assertion
`keyBits == ot_setup.base_receiver_inputs`. The check was not removed; the
also-supported **Tinier** backend was used.

Fixture: ASCII key `synthetic-key-not-for-clinical-use` (34 bytes), JSON
`["patient-identity-v1","Änne","Beispiel","1980-01-02","synthetic-center-a"]`
(76 UTF-8 bytes). Target HMAC:
`41697558cc6b66f36faab6bf713575f3d59041e04b58b54859e1ff7c3156bfe5`.
Public epoch:
`e143ca424402580248ebc5d10a68d5f469bde4de5ddd8d04febad3b6a6a55250`.
The key is a public test fixture, not an acceptable production secret.

In the external release directory, place the `.mpc` in `Programs/Source` and
the pinned Bristol circuits in the expected `Programs/Circuits` location.
`Player-Data/Input-P0-0` contains the key's decimal byte values followed by the
32 decoded bytes of the **target digest**, separated by spaces.
`Input-P1-0` contains the JSON message's decimal UTF-8 bytes, repeated `COUNT`
times. Only the digest is decoded from hex, never the configured key. These
synthetic files are prepared exclusively outside the application. Reproduction
requires an isolated laboratory environment.

```text
python3 compile.py -M -B 64 patient_matching_2pc 34 76 COUNT EPOCH
bin/Linux-adx/tinier-party.x 0 PROGRAM -N 2 -pn 19434 -h 127.0.0.1 -OF .
bin/Linux-adx/tinier-party.x 1 PROGRAM -N 2 -pn 19434 -h 127.0.0.1 -OF .
```

Here `EPOCH` is the value above and
`PROGRAM=patient_matching_2pc-34-76-COUNT-EPOCH`, with placeholders substituted.
The final two processes run concurrently under a supervisor with a 240-second
limit, a 2-GiB address-space limit per process, and disabled core dumps. The base
port and following port must be free. `-M` preserves memory order during
compilation, avoiding the initially observed compiler warning. Inputs are
bytewise; the circuit explicitly converts to the SHA-256 component's bit order.
Public limits: 1–1024 key bytes, 1–4096 payload bytes, 1–100 candidates;
size/count and epoch are not hidden.

No additional privacy padding: all candidates in an instance have the same
public byte length; SHA-256 uses its normal cryptographic padding. B serializes
outside the circuit. A's key and target remain private circuit inputs. The epoch
is checked **inside** the circuit with the same HMAC function. There are no
exported or cached HMAC states.

Both parties receive `epoch_valid`, followed by a gated match bit for each
candidate. `epoch_valid=0` must not be interpreted as a nonmatch. This is not a
finished workflow result type. Output is incremental: a later abort cannot
retract partial results already output. By contrast, the reference prevents
domain partial results when incompleteness is known. There are no UUIDs,
signatures, replay database, authorization checks, or JSON format validation
in the circuit. A manipulated output transcript is unauthenticated. Malformed
input/transport fuzzing and a full interruption test matrix were not performed.

The test connection uses localhost as its peer address, **without mTLS**; this
does not rule out another listening interface. This is not a transport profile
for a hostile clinical network. Production isolation, separate file permissions,
secure input adapters, logging, swap, crash dump, and backup rules remain open.
The laboratory harness holds synthetic input files and outputs; this pattern
must not be copied for real secrets.

### Measurements and backend checks actually performed

Intel Core i5-12450H, 12 logical CPUs, 16 GiB RAM, Linux 6.12.112 x86_64,
Python 3.12.13; MP-SPDZ v0.4.4 Linux-adx binary. All measurements are single
local runs, not averages or extrapolations. Batch candidates repeat the same
synthetic 76-byte message; all bits are 1. They measure neither heterogeneous
clinical search nor UUID association.

| Candidates | Compile/setup (s) | Execution wall time (s) | Runtime including preprocessing (s) | Wall time/candidate (s) | Global backend communication (MB) |
|---:|---:|---:|---:|---:|---:|
| 1 | 20.589 | 3.633 | 3.53942 | 3.633 | 581.641 |
| 10 | 9.941 | 21.392 | 21.295 | 2.1392 | 3 480.96 |
| 100 | 20.41297 | 171.81484 | 171.736 | 1.71815 | 32 482.8 |

“Compile/setup” is only circuit compilation, not cryptographic preprocessing.
Online time and cryptographic setup were **not separately instrumented**: runtime
includes both. Compilation plus measured wall time is 24.222 s for 1, 31.333 s
for 10, and 192.22781 s for 100 candidates; one-time download, installation,
and input preparation are excluded. MB denotes global transmitted volumes
reported by the backend, not packet-capture measurements including all network
and TLS headers. No extrapolated value for 1,000; 1,000 was not attempted because
communication costs were already high.

The 10-candidate run preceded the switch to `-M` and showed the generic memory
order warning. Final 1/100-candidate runs use `-M`. These measurements therefore
do not form a controlled scaling series; all three runs returned the expected
bits functionally. Reliable comparisons require repetitions with identical
compiler/runtime parameters.

Synthetic raw measurements reside temporarily in
`/tmp/lx-matching-backend-1-preserved.json`,
`/tmp/lx-matching-backend-10.json`,
`/tmp/lx-matching-backend-100-preserved.json`,
`/tmp/lx-matching-backend-negative.json`, and
`/tmp/lx-matching-backend-long-key.json`; they are not durable production
evidence. The fixture, pins, and commands above enable repetition.

Peak resident set size (RSS) was not measured. The 2-GiB address-space limit
is not a memory measurement; a point-in-time RSS observation of approximately
47 MiB per process during the 100-candidate run is explicitly not a peak.
According to the backend, 100 candidates required approximately 1 984 413
communication rounds per party. Local-area/wide-area network latency and
concurrent transfers can substantially change the result.

Actually successful: Unicode JSON produces the expected HMAC; an incorrect
target yields `epoch_valid=1, match=0`; an incorrect key with a bound epoch yields
`0,0`; B's center instead of A's yields `1,0`. An additional HMAC long-key test
(RFC-4231 test vector 6: 131 bytes `0xaa`, 54-byte message) yielded `1,1`, wall
time 3.74453 s, 710.253 MB globally. These checks do not replace adversarial
protocol review. A laboratory port conflict caused a timeout; cases ran after
selecting separate ports. The timeout was not interpreted as a nonmatch. A
deliberately interrupted complete workflow with signatures and atomic relationship
persistence does not yet exist.

Final structure check: 65 manifests passed; feature validation: 37 definitions
valid. As expected, `tracker.py check hub_transfer` reports
**NOT PRODUCTION READY (5/12)**. Whole-project Pyright errors remain open; the
isolated reference files are type-checked. `.mpc` is checked by the MP-SPDZ
compiler, not Pyright. Ruff for the reference files and the whitespace diff
check scoped to changed files passed.
