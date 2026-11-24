# Patient identity and private cross-center matching

Binding scope: [HubTransfer.yml](../feature-tracking/HubTransfer.yml),
criterion `local_identity_hash_boundary`, and
[Anonymization.yml](../feature-tracking/Anonymization.yml),
criterion `identity_hash_response_boundary`.

Patient and examination hashes as well as identity fingerprints remain on the
source node. They must not be transmitted to the hub, including inside nested
metadata, retries, or encrypted transfer packages. The sender rejects such
payloads before the network call; the receiver rejects them before
registration. File content checksums remain permitted for media integrity.

## Zentrumsweite lokale Patientenpseudonyme

Neue Patienten aus der hashbasierten Importanlage erhalten durch
`get_patient_uuid` eine deterministische UUIDv8: HMAC-SHA-256 mit dem validierten
lokalen Identitätssalt über die kompakte UTF-8-JSON-Liste
`["center-patient-uuid-v1", center_key.strip(), patient_hash]`.
Die ersten 128 Digest-Bits werden mit UUID-Version 8 und der RFC-Variante
formatiert; 122 Bits bleiben für den Pseudonymwert. Der Patientenhash bleibt
unverändert, einschließlich unterstützter nichtleerer Legacy-Kennungen.
Gleicher Hash, Zentrumsschlüssel und Schlüsselgeneration ergeben auf mehreren
Quellmaschinen dieselbe UUID. Die Zentrenzuordnung erfolgt niemals über lokale
Datenbank-IDs. Die vorgelagerte Erzeugung des Patientenhashs muss ebenfalls
übereinstimmen; externe Legacy-Kennungen können dort noch lokale IDs enthalten.

Die Importanlage sperrt die Zentrumszeile während Auflösung und Erstellung.
Bestand und historische Hash-Aliase werden zuerst aufgelöst; UUIDs werden bei
Rotation oder Korrektur nicht neu berechnet. Direkte ORM-Anlage ohne diesen
Importpfad und Patienten ohne Identitätshash behalten den bisherigen Standard.
Empfangene Hub-UUIDs bleiben explizit gesetzt. Eine Bestandsmigration erfolgt
nicht; deshalb konvergieren bereits unabhängig angelegte Patienten nicht
nachträglich. Nach Rotation müssen Bestandszuordnungen zwischen Quellmaschinen
erhalten bleiben; reine Neuberechnung stellt diese Kontinuität nicht her.

Die [EDPB-Leitlinien 01/2025](https://www.edpb.europa.eu/system/files/2025-01/edpb_guidelines_202501_pseudonymisation_en.pdf)
liegen hier als Konsultationsfassung vom 16. Januar 2025 zugrunde.
Absätze 88–89 und 107 behandeln geheime, kryptografische Einwegfunktionen;
115–121 verlangen zweckgebundene, notwendige und rechtmäßige Verknüpfung.
Dieser technische Entwurf begründet keine Rechtsgrundlage. Das Zentrum muss
Zweck, Empfänger, Berechtigungen und Aufbewahrung festlegen und Originaldaten
sowie Schlüssel von den geschützten Empfängern getrennt halten. Die UUID ist
ein personenbezogenes Pseudonym, keine garantierte Anonymisierung. Kenntnis
des Schlüssels oder zusätzlicher Daten ermöglicht Kandidatenabgleich; gleiche
demografische Merkmale beweisen keine Personenidentität. Schlüsselverteilung
oder eine Ausweitung auf andere Zentren wird hier nicht aktiviert.

## Hub boundary

The persisted local patient and examination UUIDs (Universally Unique
Identifiers) are local pseudonyms as well and do not leave the source node.
Transfer contract 7.0 transmits exclusively **hub-bound pseudonyms** in the
fields `patient_uuid` and `examination_uuid`:
`get_hub_record_pseudonym` in `endoreg_db/utils/hashs.py` computes HMAC-SHA-256
locally under the local identity salt over record kind, local UUID and the
target hub's node key, and formats the result as UUIDv8 (RFC 9562). Media and
study transfers use the same derivation
(`endoreg_db/services/hub/hub_pseudonyms.py`).

Follow-up examinations are assigned on the source node through the patient hash
of the same local UUID and therefore receive the same pseudonym at the same hub.
The hub can group them but can reconstruct neither the local UUID nor the
patient hash; pseudonyms of different hubs or salts cannot be linked without
the salt. A link to the local record only arises through the local salt. The
shared contract rejects every other UUID version. Version checks alone cannot
distinguish new local UUIDv8 values from hub pseudonyms: senders must still use
the hub derivation, and the receiver cannot verify that derivation itself.
Versions 3.0 to 5.0 are rejected. Hub rows received under 5.0 still
contain local UUIDs and require their own reviewed cleanup procedure. A salt
rotation changes the hub pseudonyms; continuation at the hub then requires an
explicit migration. Existing local patients, hash aliases, identity keys and
import workflows are preserved.

The [benchmark](../scripts/benchmark_sha256.md) shows a candidate search with a
known key and a bounded input space, not a general mathematical inversion of
SHA-256. The transfer block nevertheless protects against disclosure of
guessable patient identity. Transport encryption does not replace this
boundary.

Central salt provisioning is disabled; see
[identity keyring](identity_ring_enrollment.md). Salts that were already
distributed or hub data that was already stored are not removed by a source
code change. Cleaning up existing data or rotating keys requires its own
reviewed migration procedure and must not break local re-identification.

## Approach A: privately compare existing HMACs

The [separate evaluation](patient_matching_2pc_assessment.md) examines exactly
the existing HMAC over name, date of birth and **source center A**, under the
existing key **of A**. Center B needs its authorized original attributes for
this; its own HMAC is not sufficient. Such a two-party computation does not
require a new shared central identity key. It can establish tuple equality but
does not prove a unique real person. The isolated experiment belongs to
`existing_hmac_2pc_evaluation` in the tracker and changes neither UUIDs nor
local hashes nor the production approval.

Hub/Vault colocation on gs-02 does not rule out a direct A/B comparison. What
matters is whether an administrator can read both complete private inputs; the
current LuxNix configuration provides shared administrative SSH access whose
effective privileges still need to be reviewed. The following VOPRF sketch is a
**different procedure with a different function and migration**, not a
compatible replacement for the existing HMAC.

## Private matching of verified shared patient identifiers

The requested additional use case is discovering shared patients through a
**verified shared identifier with an issuing authority**. For this production
contract, name and date of birth are not verified identifiers. The separate
Approach A evaluation may compare them as a tuple. Scope and evidence for
verified identifier matching belong to the criterion
`cross_site_private_patient_matching` in
[HubTransfer.yml](../feature-tracking/HubTransfer.yml).

An identifier needs a binding issuer namespace, its normalization rules and
traceable verification. Leading zeros must not be stripped wholesale.
`PatientExternalID.origin` and `external_id` currently only store origin and
value; they do not prove verification. A source system must not automatically
report such records as verified.

### Limits of the existing infrastructure

The review of the checked-in LuxNix configuration on 19 October 2026 shows:
`ansible/inventory/host_vars/gs-02.yml:84` enables the central hub; the same
host enables Vault from line 146. The roles in
`modules/nixos/luxnix/vault/default.nix:403` allow node-specific certificates
and secret access but do not provide private matching. According to
`docs/vault-hub-machine-enrollment.md:47`, no human login or enrollment
administrator role is provisioned. This does not prove an independently
administered key authority. This finding describes source configuration, not a
verified runtime state.

An additional process, container or Vault path on the same host does not
protect against that host's administrators or a full host compromise. An
independent matching authority requires separate administrator access,
deployment rights, key custody, backups and recovery rights. The hub must not
be granted permission to evaluate identity candidates.

### Protocol requirements

A verifiable oblivious pseudorandom function (VOPRF) according to
[RFC 9497](https://www.rfc-editor.org/rfc/rfc9497.html) is a candidate building
block. It hides the input from the key service and allows the client to verify
the evaluation against a previously provisioned trusted public key. On its own
it is not a complete secure patient matching protocol.

1. The source verifies the identifier according to the defined issuer policy.
   A version-bound, unambiguously encoded matching input contains purpose,
   matching scope, issuer and normalized identifier. Local UUIDs and source
   nodes do not belong in this input, since otherwise no cross-site equality
   comparison is possible.
2. The source blinds the input and has it evaluated by the independently
   administered service. It verifies the cryptographic proof and the approved
   key generation. Errors and unknown keys abort the operation.
3. A separately signed message binds the result to source node, patient UUID,
   matching scope, key generation, verification receipt, recipient, expiry and
   replay protection. Signing keys and key-exchange keys remain separate.
4. The hub records confirmed relationships between hub-bound pseudonyms. It
   does not overwrite patient identity, does not silently merge records and
   does not confuse document content equality with patient equality.
   Conflicts, revocations and key migration require explicit rules.

VOPRF proofs confirm a correct key computation, **not** the authenticity of the
patient identifier. A node signature identifies the sender but does not prove
the truth of its statement. Against malicious sources, a trusted verification
boundary bound to the evaluation or a separately reviewed credential protocol
is required. Custom curve arithmetic or self-designed zero-knowledge proofs are
not an acceptable shortcut.

Equality tokens reveal equality and recurrence to the hub. They are sensitive
pseudonyms and not anonymous data. Evaluation rights, volume limits, auditing
without identifier values and a ban on freely queryable existence oracles limit
abuse but do not remove the risk of authorized guessing attempts. Anyone who
obtains the key and the token inventory together can test candidate
identifiers offline. The operating model must explicitly account for this
boundary.

The existing X25519 transport encryption stays separate from matching. Password
hashes, Django `SECRET_KEY`, `NetworkNode.shared_secret` and local master keys
are neither matching keys nor seed values for asymmetric keys. Private keys are
not derived from patient attributes. The additional matching interface must not
broadly relax the existing media and browser transfer blocks.

## Browser APIs and exports

Browser routes use local numeric patient/examination IDs and case UUIDs. Patient,
case, timeline, anonymization metadata and cohort responses omit identity hashes,
including shortened displays. Case creation resolves the supplied patient ID only
within the authenticated user's authorized centers. Cohort grouping continues to
use hashes internally; those grouping keys are never serialized.

FHIR export version 2.0 identifies persisted local patients and examinations
without hashing or publishing their demographic identity hashes. Selected-case
JSON/XLSX contract 2.0 uses `patient_id`; study workbooks contain local patient and
examination IDs. These are source-local identifiers, not cross-node matching keys.
Workbook builders validate their contracts before serialization. The HTTP guard
still rejects identity-hash fields, but no longer rejects every spreadsheet.
Annotation and timestamp CSV streams validate their fixed column headers before
publication. Other deferred metadata streams remain blocked until they have an
explicit validated contract; binary media streaming and explicit
file-integrity checksums remain available. Displayed timeline/case media names use
resource IDs rather than protected source filenames.

Deploy the shared contracts, backend and frontend together. The response guard
cannot roll back a completed local mutation; do not automatically retry writes
when an incompatible old client receives a blocked response.

## Local execution of exact HMAC matching

The service `endoreg_db.services.patient_identity.matching` connects the local
input snapshots, the registered session and the MP-SPDZ worker. Scope and
approval remain governed by `guarded_patient_matching_resolver` in
[HubTransfer.yml](../feature-tracking/HubTransfer.yml). The service is not an
HTTP release and does not replace the contract for verified issuer-qualified
identifiers. Exact tuple equality does not justify an automatic merge of
persons.

A uses `prepare_source_input`, B uses `prepare_candidate_input` with A's
canonical center identifier. The `MatchingAuthorization` sets derived from local
authorization decisions must not be taken from unverified peer requests.
`SourceMatchingRecords` and `CandidateMatchingRecords` respectively verify the
unchanged local references at registration, execution and result acceptance. B
needs received and locally verified original attributes; B's local HMAC is no
substitute for them.

Both endpoints register the same public `MatchingRequest` with
`register_request` in their **own respective database**. The request already
contains the candidate UUID references and their count: this implementation
does not hide this metadata from A. The private inputs remain separate.
`run_matching_worker` claims the session atomically exactly once and holds no
database transaction open during the network computation. A crashed execution
stays claimed; resumption requires a new authorized session and consumes the
existing source UUID request budget. This local budget is not a cross-center
coordinated protection against all adaptive queries.

The circuit `scripts/reidentification/patient_matching_resolver.mpc` is
compiled for public key/message lengths and the authenticated generation
fingerprint:

```text
compile.py -M -X patient_matching_resolver KEY_SIZE LENGTHS_CSV EPOCH_HEX
```

The backend uses MP-SPDZ v0.4.4/MASCOT and TLS. Key bytes are processed
unchanged; SHA-256 and HMAC follow the existing function in
`endoreg_db/utils/hashs.py`. The circuit additionally checks the public
generation fingerprint and opens only one aggregated result value to B. Both
roles additionally input the domain-separated SHA-256 digest of the same
complete public request; the circuit compares these digests and discards
crossed sessions as incomplete. A initially receives only the completion
marker. Length and count are public; JSON form and record origin are checked by
the trusted local adapter, not cryptographically proven in the circuit.

Each endpoint requires its own protected runtime directory: exactly its private
TLS key, both public certificates including pinned OpenSSL truststore entries,
the pinned binary, circuit source, SHA-256 circuit, schedule and all bytecode
tapes. `MatchingPolicy.circuit` and `MatchingMetadata.circuit` contain the
SHA-256 file hash of the enrolled circuit source. `BackendJob.artifacts` comes
from trusted local provisioning, not from the peer. The runtime directories are
reserved exclusively for these workers. A non-blocking directory lock prevents
cooperating workers from using the same MP-SPDZ state in parallel.
Administrators, backups, core dumps and state left behind by the backend belong
to the protected operating boundary. Separate directories on a test host do not
prove administrative independence.

B receives `MatchingResult`; timeouts or backend errors are stored as
`aborted_or_incomplete` only while the local bindings are still intact. Stale or
revoked bindings raise an error and create no relationship. An invalid
generation comparison becomes `key_epoch_unavailable`, never `no_match`.
Missing local keys before execution raise `MatchingKeyEpochUnavailable`.

After B's signature, A may countersign through `countersign_source_result` if
A's local worker completed successfully and the recipient signature is valid.
From this explicitly released message onward, A also sees the result.
**A cannot independently verify B's privately opened result value.** The
countersignature therefore authenticates B's statement and A's local
participation; it is not a generally verifiable evaluation proof against a
lying B. `accept_result` verifies both signatures and the locally stored
completion and creates a separate UUID relationship only for `single_match`.
Patient UUIDs, identity hashes and artifact hashes are not modified.

The optional native tests run against an independently installed MP-SPDZ v0.4.4
source tree; without the explicit variable they are reported as skipped, not as
a passed cryptographic check:

```sh
LX_MPSPDZ_ROOT=/path/to/mp-spdz-0.4.4 .devenv/state/venv/bin/pytest \
  tests/services/test_patient_matching_backend.py -q
```

The ORM orchestration is tested separately in `test_patient_matching_workflow.py`;
its controlled worker responses are not 2PC evidence. The documented
implementation and security assumptions of the
[MP-SPDZ version](https://github.com/data61/MP-SPDZ/tree/113c4e47dd94bca9ecab029d2f0abe649bea5c52)
apply to the protocol used.

For received DateTimeField dates of birth, the ORM adapter reconstructs the
clinical calendar date in the configured Django time zone before encoding,
matching the existing SensitiveMeta hash path. The encoding function in
`hashs.py` is not modified in the process.
