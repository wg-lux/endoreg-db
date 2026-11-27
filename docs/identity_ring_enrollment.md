# Local identity keyrings

Contract: [Anonymization.yml](../feature-tracking/Anonymization.yml),
criterion `central_identity_salts_disabled`. Patient identity remains local;
see the [transfer boundary](source_patient_matching.md).

Central provisioning and distribution of identity salts are disabled.
LuxNix must not generate center salts or grant read access to them.
`centerIdentitySalt` and `identitySaltViaSecretSpec` are rejected when enabled.
The former central launcher and provisioner abort before secret access or
network communication, respectively. The backend loader rejects
`DJANGO_IDENTITY_SALT_BUNDLE` and `secretspec` mode.

Existing local `DJANGO_SALT_FILE` and
`DJANGO_IDENTITY_SALT_KEYRING_FILE` configurations remain supported. All local
identity writers must use the same established generations. Missing keys must
not be automatically regenerated or replaced with centrally obtained values.
Historical generations remain available for local recognition until a verified
migration permits their removal.

On `Configured identity salt does not match legacy patient hashes`, preserve
existing keys and records. Establish the original local generation from verified
recovery documentation; neither delete hashes nor generate a new salt to bypass
the error. The procedures in [secret_rotation.md](secret_rotation.md) remain
separate, explicit migrations.

The source change activates no hosts and revokes no previously distributed
secrets. Before a coordinated rollout, check that hosts formerly supplied
centrally retain their local keys. No automatic deletion, rotation, or rekeying
is part of this block.
