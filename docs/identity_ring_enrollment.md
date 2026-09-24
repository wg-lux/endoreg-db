# Setting up an Identity Keyring on gc-02 and gc-10

Read [Key rotation and recovery](key_rotation.md) before provisioning or migrating.

An identity keyring links a new Django salt with explicitly allowed older Django salt generations. This enables compatible applications to recognize existing patients and create new identities using the new Django salt.

This setup specifically affects identity hashes. The separate rotation of media files and Django signature keys is described in [secret_rotation.yml](secret_rotation.yml). Host verifications and open shares are documented exclusively in [Anonymization.yml, criterion online_secret_rotation](../feature-tracking/Anonymization.yml).

## 1. Verify the Correct Machine and Existing Django Salt Inventory

| Machine | SSH Target |
| --- | --- |
| gc-02 | `admin@172.16.255.102` |
| gc-10 | `admin@172.16.255.110` |

After logging in, verify `hostname`. `sudo` is required for setup and verification.

The following legacy setup applies exclusively if existing identity hashes were verifiably created using `default_salt`. Any unknown or different existing Django salt must first be clarified and preserved. Both machines receive independently generated new Django salts.

Before activation, all identity writers must use the keyring-compatible backend version: web service, import processes, Celery workers, and maintenance commands. Legacy writers must not run concurrently with the Django salt migration. Updating a running legacy application by simply copying a manifest file is insufficient.

## 2. Enable Legacy Setup in LuxNix

The host configuration provides the following option:

```nix
services.luxnix.lxAnnotateLocal.django.enrollLegacyDefaultSalt = true;

```

For inventory-generated configurations, add the setting under `host_services` in `ansible/inventory/host_vars/gc-02.yml` or `gc-10.yml`, respectively:

```yaml
host_services:
  luxnix.lxAnnotateLocal.django.enrollLegacyDefaultSalt: "true"

```

Include this entry in the existing mapping while preserving the remaining host settings. Activate the configuration using the established LuxNix rendering and deployment pipeline from a verified, persistent source. An older LuxNix version lacking this option must first receive the corresponding configuration change.

Managed provisioning creates the following private files:

| File under `/etc/secrets/vault/` | Content |
| --- | --- |
| `lx_annotate_identity_active` | New random active Django salt |
| `lx_annotate_identity_legacy_default` | Previous `default_salt`, strictly as a retired generation |
| `lx_annotate_identity_keyring.yml` | References to both files |

All three files belong to the service user and have mode `0600`. Previously provisioned Django salts are preserved. If the active Django salt is missing after publishing the manifest, it must be restored from authorized backups; replacing it with an arbitrary salt will alter identity mappings.

The manifest contains no secret values:

```yaml
schema_version: 1
active: /etc/secrets/vault/lx_annotate_identity_active
retiring:
  - /etc/secrets/vault/lx_annotate_identity_legacy_default
allow_legacy_default_salt: true

```

LuxNix exports the manifest path as `DJANGO_IDENTITY_SALT_KEYRING_FILE` for service and maintenance processes. `default_salt` is never entered as the active Django salt in `DJANGO_SALT_FILE`.

## 3. Verify Setup Without Exposing Secrets

Run the following commands on each target machine:

```bash
sudo stat -c '%U:%G %a %n' \
  /etc/secrets/vault/lx_annotate_identity_active \
  /etc/secrets/vault/lx_annotate_identity_legacy_default \
  /etc/secrets/vault/lx_annotate_identity_keyring.yml

sudo rg '^DJANGO_IDENTITY_SALT_KEYRING_FILE=' \
  /var/lib/lx-annotate/.env.systemd

sudo -u endoreg-service-user \
  /var/endoreg-service-user/lx-annotate-wheel/.venv/bin/python -c \
  'from pathlib import Path; from endoreg_db.config.secret_keyring import load_keyring; ring = load_keyring(Path("/etc/secrets/vault/lx_annotate_identity_keyring.yml"), kind="identity"); print({"valid": True, "retiring_generations": len(ring.retiring), "active_is_legacy": ring.active == b"default_salt"})'

systemctl show \
  lx-annotate-runtime-env.service \
  lx-annotate-migrate.service \
  lx-annotate.service \
  lx-annotate-celery-beat.service \
  -p Id -p ActiveState -p SubState -p Result

```

During initial legacy setup, the loader check must yield `valid: True`, `retiring_generations: 1`, and `active_is_legacy: False`. The loader check confirms the manifest, but does not replace verifying that all running processes have actually picked up its path. Afterwards, run the existing startup and acceptance checks from LuxNix `Diagnostics.md`.

Do not display secret files or the full `.env.systemd` using `cat`. Similarly, `luxnix-secrets generate --secret …` may output secret content after generation; use the verified managed provisioning workflow for this setup.

## 4. Migrate Existing Identities Separately

Setup alone does not constitute a completed inventory migration. First, run the dry run in the installed production service context:

```bash
python -m django rotate_identity_salt

```

Blocked groups require review. For previously deleted identifiers, a private, verified source file is required; the format is defined in [secret_rotation.yml](secret_rotation.yml). Once the dry run preview is resolved, apply the migration within the same service context:

```bash
python -m django rotate_identity_salt --apply

```

Never execute these commands against production data using test settings or from an arbitrary development checkout. Patient and examination IDs are preserved; incomplete or conflicting mappings will be rejected.

## 5. Remove Retired Generation Only After Full Verification

The old Django salt remains in the keyring until inventory migration, examiner assignments, and all dependent writers are verified. Always provision additional generations in new private files and swap only the manifest atomically. Do not overwrite active Django salt files or replace them using `regenerate`.

In the event of an error, preserve the existing manifest and its Django salt files. Resolve the first failing startup step; do not generate a new Django salt to bypass validation. Rolling back to an older application without keyring support is not a safe fallback strategy once new write operations have occurred.

## Import fails with a legacy patient-hash mismatch

`Configured identity salt does not match legacy patient hashes` means the
configured salt cannot reproduce the selected legacy identity from its stored
source fields, and no configured keyring reader matches it. This is separate
from the media encryption master key. Possible causes include a missing previous
salt generation or changed legacy source fields; the exception alone does not
prove which occurred.

Preserve the existing salts and identity rows. Verify that the web, import workers
and maintenance commands use the same identity keyring. Restore the known previous
salt as an explicitly configured retiring generation, then run the migration
preview above. Use default-salt enrollment only when that legacy salt is verified.
Unmatched or incomplete identities require review before applying migration.
Do not clear hashes, disable the guard, or generate a replacement salt to retry.

A subsequent `failed import staging` / `rejected_outside_roots` message is a
separate report staging defect in older endoreg_db releases. Report anonymizer
output belongs in `get_runtime_paths().import_anonymized_report` until encrypted
publication succeeds, not in `anonym_report`. Update the backend before retrying;
do not add final storage directories to the staging cleanup allowlist. Existing
rejected artifacts need ownership and publication-reference review before cleanup.
