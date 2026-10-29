# Reversibility and collisions of the Sensitive Meta Hash

## Current assessment and policy of October 19, 2026

Patient and examination hashes are **local pseudonyms**, not anonymous export
identifiers. They remain at the source node, including identity fingerprints and
historical hash aliases. An encrypted transfer package, shortened hash, or renamed
pseudonym does not permit disclosure to the hub, browser, or research export.
File content checksums serve a different integrity purpose; their admissibility
does not establish anonymity of the exported data.

The binding contracts are [local patient identity](../../docs/source_patient_matching.md)
and [local identity keyrings](../../docs/identity_ring_enrollment.md). Scope and
acceptance remain exclusively in [Anonymization.yml](../Anonymization.yml),
particularly `identity_hash_response_boundary`, `central_identity_salts_disabled`,
`strict_legacy_hmac_inventory`, and `compromised_identity_salt_migration`, and
[HubTransfer.yml](../HubTransfer.yml), `local_identity_hash_boundary`.

This supersedes the former plan for centrally distributed center salts.
Vault/SecretSpec distribution of identity salts must not be reactivated.
Established local keys remain; missing material is neither automatically
regenerated nor replaced with centrally obtained values. Historical salts are
still needed at the source center for recognition, even after direct identifiers
have been deleted. Supporting them permits neither old authentication keys nor
new identities with compromised salts. Historical media decryption keys serve
a separate purpose.

### Current calculation and limits of the claims

The current demographic write path directly uses:

```text
K = UTF-8 of the validated local identity secret
M = UTF-8(JSON(["patient-identity-v1", trim(first_name), trim(last_name),
               ISO(date_of_birth), trim(center_key)],
              ensure_ascii=False, separators=(",", ":")))
patient_hash = hex(HMAC-SHA-256(K, M))
```

Evidence: `endoreg_db/utils/hashs.py`, functions
`get_patient_identity_fingerprint` and `calculate_patient_hash`. The date of
birth appears once in the patient message. JSON field boundaries eliminate the
legacy ambiguity described below; former double hashing is not the current
patient contract. The examination hash uses a separately tagged HMAC message
with patient HMAC, examination date, and center.

`endoreg_db/utils/verify_salt.py::validate_identity_salt` checks active keys for
minimum length, blocked patterns, and byte diversity. These checks do not prove
random generation. Explicitly registered historical read generations remain
supported in `hashs.py::_get_active_salt_bytes`. The loader
`config/secret_keyring.py::configured_identity_keyring` rejects central bundles
and `secretspec` mode. The response/transfer boundary is in
`services/privacy/response_boundary.py`; complete contract details and content
checksum exceptions belong in the sources linked above.

With a known secret, bounded candidate search remains possible even against
HMAC. Homonyms remain ambiguous in the domain; spelling variants can split
identities. The idealized birthday estimate explained below applies to the
current single 256-bit HMAC; the double-hash upper bound concerns only the
historical algorithm. Neither this model calculation nor the transmission block
proves anonymity of complete exports.

Migration and local recognition must retain historical generations and
center-bound aliases, reject conflicts, and preserve clinical relationships.
Hub reidentification using transferred hashes is no longer an admissible
migration goal. A different transfer contract requires a separate domain and
technical definition. Rotation does not retroactively protect stolen hash/key
copies; a code change deletes no previously distributed secrets or hub data
and establishes no production migration state.

### Reproducibility of this reconciliation

On October 19, 2026, only documentation was reconciled with the local working
tree. The existing test `tests/utils/test_sensitive_meta_hash_security.py` now
explicitly checks legacy encoding collisions **and their separation in the current
HMAC**, repetition, salt changes, 366 dates of birth, and a registered weak
historical salt. Execution: `devenv shell -- .devenv/state/venv/bin/pytest
tests/utils/test_sensitive_meta_hash_security.py -q --tb=short --show-capture=no
--disable-warnings`: **11 passed, 1 warning**, 0.26 seconds; not a benchmark or
evidence of fleet state, restore, or complete export protection.

The [implementation assessment of October 19, 2026](../../docs/endoreg-db-threat-model.md)
contains additional recipient-specific risks. Its statements about individual
export fields are a working-tree snapshot and must be checked against the newer
local identity policy; they permit no prohibited hash output.

## Historical investigation of October 16, 2026

**The following formulas, line references, and findings describe only the state
at that time.** In particular, short active salts, double hashing, and the
recommendation for a future HMAC transition do not describe the current write
path. Historical experiments remain as migration rationale; current readiness
and open acceptance decisions live only in feature YAML.

The demographic patient hash is a deterministic pseudonym. It provides no
general proof of anonymity: possible identities can be recalculated with a
known salt. The largest concrete collision source is ambiguous input field
concatenation; random cryptographic collisions are extremely unlikely under the
ideal model. The existing HMAC fingerprint protects the regular persistence path
against different field partitions, but cannot distinguish two real people
with identical identity data.

Investigation date: October 16, 2026. Basis: working tree at commit
`08c1ac0da110343dfc6d0314b24998a1937d9aeb`, including preexisting uncommitted
changes. Methodological template:
`codex://threads/01a143b2-26ac-7ef2-a191-a0a43760aeda`.
Requirements and approval status remain exclusively in
[Anonymization.yml](../Anonymization.yml), criterion
`sensitive_metadata_resolution`. This is a technical investigation, not
production approval or a legal assessment.

## Actual calculation

With `H = SHA-256`, `hex` as hexadecimal representation, `||` as concatenation,
first name `V`, last name `N`, ISO date of birth `D`, center name `C`, and salt
`S`, the demographic path calculates:

```text
M = UTF-8(V || N || D || C || D || S)
calculate_patient_hash = hex(H(M))
SensitiveMeta.patient_hash = hex(H(UTF-8(hex(H(M)))))
```

The date of birth appears twice. This repetition adds no entropy. The second
SHA-256 call processes the first digest's 64 ASCII hexadecimal characters, not
its 32 raw bytes. Output remains 256 bits, not 512 bits. Evidence:
`endoreg_db/utils/hashs.py:118–159` and
`endoreg_db/models/metadata/sensitive_meta_logic.py:295–318`.

The examination hash inserts the examination date before the salt and is also
double-hashed (`sensitive_meta_logic.py:321–356`). A patient hash therefore
identifies neither a report nor necessarily a single examination. External,
source-specific patient identifiers have their own path and are outside the
demographic formula above.

The additional `identity_fingerprint`, by contrast, is an HMAC-SHA-256 over a
JSON list with version tag, separate names, ISO date of birth, and numeric center
ID (`hashs.py:33–52`). The `identity_salt_fingerprint` is an HMAC under the same
key material over the constant string `endoreg-identity-salt-v1`
(`hashs.py:25–30`). The four hash/fingerprint fields each store up to 64
characters (`models/metadata/sensitive_meta.py:145–157`). HMAC is a standardized
secret-key construction; it is not equivalent to simply appending a salt.
[RFC 2104](https://www.rfc-editor.org/rfc/rfc2104.txt)

## Findings by security relevance

### 1 High relevance with a known or weak salt

A hash is not encryption with a reverse function. Reidentification can still
use forward comparison: construct candidates, calculate with the same salt,
compare with the target. If name and center are known, the search may consist
only of dates of birth. The test uses a known name, one center, and the 366 days
of 1980. The target is synthetic and explicitly part of the candidate set. A
match identifies a compatible input tuple, not necessarily a unique real person.

The value called a salt acts here as a shared secret. With a strong, unknown,
protected key, simple offline comparison without the key is impossible; an
accessible calculation service could nevertheless act as a comparison oracle.
No such service was demonstrated here. With a disclosed salt, even the HMAC
fingerprint does not prevent candidate checking. Two fast SHA-256 applications
do not replace memory-hard key derivation.

`config/secret_keyring.py:60–84` rejects empty, multiline, and certain default
values, but enforces neither minimum length nor random generation. A single
character is accepted. Anyone knowing the stored salt fingerprint can check
weak salt candidates against the public constant message without knowing a
patient name. This is not an HMAC break, but a consequence of guessable keys.
Actual production key quality was not checked. Private key files and explicit
invalid-configuration failures already exist (`config/identity_hashing.py:30–70`).

### 2 High integrity relevance of ambiguous input encoding

`AB` plus `C` and `A` plus `BC` yield the same pre-hash byte sequence when all
other fields are identical. Hash equality is guaranteed for this pair. This is
an encoding collision, not two different SHA-256 messages with the same digest.
Every partition of `ABCDEFGH` into two nonempty names yields the same legacy
hash: seven field partitions and 21 equal pairs.

Regular `SensitiveMeta.save()` logic calculates the separately encoded fingerprint
and checks existing rows with the same patient hash. Different fingerprints
raise an error; PostgreSQL serializes the check through a transactional advisory
lock (`sensitive_meta_logic.py:85–177, 518–535`). Existing integration tests check
rejection both before and after deletion of direct identifiers:
`tests/import_files/test_sensitive_identity_guards.py`,
`test_legacy_name_concatenation_collision_fails_without_partial_writes`.

This protection must not be attributed to the hash itself. For example, the
separate service `services/privacy/patient_pseudonyms.py:43–60` calculates the
legacy hash and saves directly to the Patient model without invoking the
SensitiveMeta conflict check. Its complete conflict behavior was not dynamically
tested here; a repository-wide guarantee for all write paths would be unsupported.

### 3 High domain relevance of identical demographic data

Two real people with identical first name, last name, date of birth, and center
receive the same patient hash and identity fingerprint under the same salt.
No longer hash fixes missing distinguishing data. The probability of such
homonyms requires a real distribution of identity attributes; it cannot be
derived from 256 bits or synthetic tests. The examination hash formula described
also does not distinguish multiple examinations of the same patient on the same
date.

### 4 Medium relevance of spelling variants and linkability

The hash helper does not normalize case, Unicode representation, or internal
whitespace. The same person can therefore receive multiple hashes. An upstream
serializer path trims outer whitespace, rather than the hash helper doing so
generally. The existing [input variation investigation](patient_hash_input_variations.yml)
and `tests/services/test_patient_identity_input_variations.py` document this limit.

Identical inputs remain linkable with the same key. The center name in the
legacy hash limits direct matches between differently named centers, but makes
renaming relevant to identity. The HMAC fingerprint instead uses the numeric
center ID. This is not general protection against linkage through auxiliary
knowledge. While direct identifiers remain stored, authorized database access
can also read the mapping directly; no hash inversion is needed.

## Quantitative collision probability

SHA-256 has 256 output bits. Security analysis must assume distinct hash
messages; demographic equality and identical encodings are excluded.
[NIST FIPS 180-4](https://nvlpubs.nist.gov/nistpubs/FIPS/NIST.FIPS.180-4.pdf)

Under the model of independent, uniformly distributed 256-bit outputs, the
probability of at least one collision among `n` distinct messages is:

```text
p = 1 - product(k = 0 … n-1, 1 - k / 2^256)
p ≈ 1 - exp(-n(n-1) / (2 × 2^256))
p ≈ n(n-1) / (2 × 2^256)   for very small p
```

| Distinct messages | Single idealized 256-bit hash | Two idealized hash stages, approximate upper bound |
| --- | --- | --- |
| 1 million | 4.318 × 10⁻⁶⁶ | 8.636 × 10⁻⁶⁶ |
| 1 billion | 4.318 × 10⁻⁶⁰ | 8.636 × 10⁻⁶⁰ |
| 1 trillion | 4.318 × 10⁻⁵⁴ | 8.636 × 10⁻⁵⁴ |

The second column directly applies only to one idealized stage. With the actual
double hash, different messages can collide internally, or different internal
digests can collide externally. A union bound in a model of two ideal stages
yields roughly twice the small value. Both stages here use the same function;
this is a model calculation, not proof of implementation independence. Double
hashing does not double security strength. The 50% threshold for a single ideal
256-bit hash is approximately `4.01 × 10^38` messages. This is not a practical
patient threshold.

For a search for a particular identity, the candidate set is decisive instead.
With `K` equally probable known candidates and a known salt, the average number
of comparisons until a match would be `(K+1)/2`. Real names and dates of birth
are not uniformly distributed. A reidentification rate or seconds per patient
would be unjustified without a corresponding experiment.

## Controlled experiments and reproduction

Tests in `tests/utils/test_sensitive_meta_hash_security.py` call the actual
model calculator and central hash helpers. They use unsaved models, public test
values, and an explicit experimental salt; no patient database or production
key material is read as an experimental source.

| Experiment | Assertion |
| --- | --- |
| Repetition and salt change | Identical input reproduces the hash; changing the salt changes it in the tested case |
| Seven field partitions | Seven equal legacy hashes; all six alternative partitions have a different fingerprint from the reference |
| 366 dates of birth | All 366 hashes differ in this series |
| Known salt and target in date space | Exactly one compatible date of birth is found among 366 candidates |
| Three short salt candidates | The target salt is recognized solely from the salt fingerprint |

These are eleven test cases, not eleven independent clinical samples. A finite
experiment without a random collision proves neither collision freedom nor a
population error rate. Artificial name partitioning is deliberately adversarial;
it does not estimate its frequency in real data. The date series is deterministic
and not representative of a patient population.

Execution on October 16, 2026: **11 passed**, one existing Pytest plugin warning,
0.11 seconds according to Pytest (not a benchmark). Full Pyright: 0 errors,
24 warnings outside the new test file. Ruff passed; 65 structure manifests and
37 feature definitions valid. Execution used the already installed Nix library
paths for PostgreSQL and GCC, with Binutils in `PATH`, matching the local runtime
prerequisite from the template thread. Reproducible findings rest on test
assertions, not timing values.

```sh
.devenv/state/venv/bin/pyright
.devenv/state/venv/bin/pytest tests/utils/test_sensitive_meta_hash_security.py -q
.devenv/state/venv/bin/python scripts/lint_structure.py
./feature-tracking/tracker.py validate
```

Database and concurrency claims additionally require running the existing
identity, salt rotation, and PostgreSQL concurrency tests in a configured test
environment. This review does not replace such integration testing.

## Architectural implications

The existing versioned HMAC with separate field encoding is a more appropriate
basis for a future identity key. A transition requires explicit migration of
existing relationships and salt generations; simply replacing the legacy hash
helper would change stored identities. Strong, randomly provisioned keys and
minimal access to hashes, fingerprints, and calculation functions limit
candidate attacks. An unknown secret must not be treated as public merely
because it is called a “salt.”

All identity-producing write paths should use the same conflict check.
Demographic equality remains a linkage hint and requires domain review or a
verified external patient identifier when ambiguous. Unchecked normalization
or automatic merging would not be a safe correction.

Publishable core statement: the examined implementation generates reproducible
pseudonyms, exhibits constructively demonstrable collisions in its legacy field
encoding, and permits recognition within predefined candidate sets when the salt
is known. Its theoretically low cryptographic collision probability must be
strictly distinguished from these domain and encoding risks.
