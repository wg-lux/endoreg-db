# Key rotation without losing existing data

Rotate one secret family at a time. Deployment readiness is tracked in
[Anonymization.yml](../feature-tracking/Anonymization.yml), criterion
`online_secret_rotation`.

Cross-repository ownership and ordered rotation gates are defined only in
[LuxNix secrets-management.yml](https://github.com/wg-lux/luxnix/blob/main/secrets-management.yml).
LuxNix provisions private generations and activates consumers; LX-Annotate
converts the environment; endoreg-db owns keyring validation and data migration.
The catalog checker validates all three source checkouts without reading secrets.

| Secret | Protects | Required overlap |
| --- | --- | --- |
| Identity salt | Patient, examination and examiner hashes | Keep verified previous salts as retiring readers until all identities and references are accounted for. |
| Media master key | Encrypted files and wrapped content keys | Keep previous keys until every file is migrated and authenticated, including backups and HLS. |
| Django signing key | Sessions and signed tokens | Planned rotation may retain verification fallbacks; a compromised key must not remain trusted. |
| LUKS key | Encrypted filesystem | Separate LuxNix/storage procedure; application rotation does not rotate LUKS. |

## Before changing a key

1. Identify each host, deployed application/backend version, service user, database
   and encrypted storage root. Verify every writer supports keyrings. Do not run
   old identity writers alongside a migration.
2. Inventory active and retiring generations without printing secrets. All key
   files and manifests must be private regular files, normally service-owned
   `0600`. Confirm web, workers and maintenance processes use the same manifest.
3. Make a consistent encrypted database/media backup and securely escrow the keys
   needed to restore it. Test restoration in an isolated environment. A backup
   encrypted only by a lost or retired key is not a usable recovery plan.
4. Preview migration against existing data before committing to rotation. Resolve
   unknown generations, placeholder identities, erased identifiers, collisions,
   missing examination evidence and examiner mappings. Preserve an approved,
   encrypted source mapping when identifier erasure would otherwise make future
   identity migration impossible, subject to retention and access policy.
5. Rehearse with the exact software versions and a protected copy of the data.
   Record expected counts, immutable record IDs and clinical links.

## Activate and migrate

Provision a new random active generation in a new private file; retain the old
file unchanged as a retiring reader. Publish the manifest atomically using the
managed provisioning workflow. Never overwrite a key file in place or regenerate
an existing key to repair permissions. Roll out compatible readers first, verify
all service environments, then enable new writes.

For legacy identities, `default_salt` is allowed **only as an explicitly enrolled
retiring value**. See [identity keyring enrollment](identity_ring_enrollment.md).
Do not assume an environment variable alone changes Django settings.

Run the following through the installed production runtime as the service user,
with the deployed environment and explicit production settings loaded. These are
command names, not instructions to use a development checkout:

```bash
python -m django rotate_identity_salt
python -m django rotate_identity_salt --review-file /absolute/private/review.yml
# Apply only after the preview and review are accepted:
python -m django rotate_identity_salt --review-file /absolute/private/review.yml --apply
```

Omit `--review-file` when no reviewed mapping is required. The current command
rolls back an incomplete apply by default. Only `--apply --allow-partial`
explicitly permits committing independently verified groups while reporting
remaining blockers. Inspect `applied` and `rolled_back` in the result. Older
endoreg-db 1.3.8 commands could commit partially without this flag: verify the
installed command before interpreting a failed exit. Preserve the preview,
counts, encrypted recovery snapshot and required legacy readers. Do not
fabricate a review mapping from guesses.

Media rotation uses its own preview and application command:

```bash
python -m django rotate_storage_secrets --include-hls
python -m django rotate_storage_secrets --include-hls --apply
```

Allow free space for replacement ciphertext. Respect active media leases; retry
deferred files. Verify plaintext integrity through authenticated storage reads.
Never pass raw encrypted filesystem paths to plaintext consumers.

## Verify before retiring anything

- Repeat the preview. Review every blocked group, pending examiner and deferred
  or unregistered artifact; partial success is not rotation completion.
- Confirm patient/examination IDs and clinical links are unchanged. Exercise an
  existing-patient import and examiner resolution without creating duplicate links.
- Verify encrypted reads, streaming and HLS across old and new generations, then
  run the host's startup/acceptance checks from LuxNix `Diagnostics.md`.
- Account for replicas, exports, historical audit references and backup retention.
  Retire an old key only after all dependents have a verified migration or a
  separately secured recovery mechanism.

## If recovery is incomplete

Normal anonymization validation now uses the same complete direct-identifier
clearing operation as privacy-policy application. It keeps committed patient
and examination hashes, collision fingerprints, and clinical foreign keys,
records an erasure receipt, and does not recreate examiner relations on later
saves. LX-Annotate's outbound worker also applies this operation after payload
validation and before registration, covering older validated metadata. Deploy
the backend and sender changes together. This is metadata erasure, not proof
that redacted report text or processed media is free of residual identifiers.

Identity salts are durable matching material. Routine credential renewal or
recovery must not regenerate them. LuxNix can provision a center-scoped salt
through the existing Vault machine-enrollment workflow, configured in inventory
with `django.centerIdentitySalt`. Moving a machine to another center does not
authorize replacing its historical salt or merging its existing patient records.
The default compatibility path retains established generations; a different
center source is an explicit migration blocker rather than silent key rotation.

File-content SHA-256 hashes are unsalted integrity/deduplication identifiers;
they are not patient pseudonyms. The legacy patient/examination helpers in
`endoreg_db/utils/hashs.py` append the configured identity secret to demographic
input, and SensitiveMeta hashes that result again. They use the center name,
not its stable key. Local keyed fingerprints detect ambiguous legacy encoding,
but are not a cross-installation identifier. Sharing a center salt therefore
also requires consistent center names and demographic inputs. Identical
demographics and same-day examinations can still be indistinguishable. A new
canonical HMAC identity format needs a reviewed alias migration, including
already exported hub records; this enrollment change does not perform one.

Keep the old readers, encrypted snapshots and original records. A salt cannot
recover missing source identifiers, and an identity-hash mismatch is not a media
master-key failure. Do not clear hashes, merge patients based on names alone,
delete records, or disable integrity guards to make a preview pass.

Salvage groups whose original identity and every linked examination authenticate
against known generations. Existing same-center examiner metadata can be used
only when it exactly reproduces the stored examiner hash and has a unique valid
mapping. Leave unverified groups unchanged and report the remaining blockers.

Do not treat new imports succeeding after a partial migration as proof that all
legacy identities are safe. A changed keyring may affect later writes even when
some older groups remain unresolved. Keep their limitations visible to operators.
