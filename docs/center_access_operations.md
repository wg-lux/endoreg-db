# Center access and central-hub video visibility

This runbook describes the operational contract for the
[`center_access`](../feature-tracking/CenterAccess.yml) feature.

## Repository and deployment status

Use [CenterAccess.yml](../feature-tracking/CenterAccess.yml) for review and rollout
evidence. Repository tests do not establish approval or enablement on a deployed host.

## Assignment, revocation, and refresh

Persistent membership changes belong in Keycloak:

1. Assign or revoke `/centers/<center_key>`; the key must exist in `Center.center_key`.
2. Sign out and in, or fully renew OpenID Connect authentication. A page reload
   does not refresh issued tokens.
3. Confirm `center_access_identity_sync_completed` for the expected user and centers.
4. After revocation, confirm HTTP `404` for resources outside remaining centers.

The administration page changes only the local cache. Each change requires a
reason, conflict protection against previously read center keys and durable audit.
Editing one's own account is prohibited. The next verified login replaces this
cache; the API never changes Keycloak roles or groups.

| Administrator | Scope |
| --- | --- |
| Django superuser | All users and centers. |
| Keycloak realm role `center_scope:global_admin` | Global center administration, without Django superuser status. |
| Keycloak realm role `center_scope:admin` | The administrator's single, unambiguous own center. |

Roles synchronize to same-named Django groups at login. `data:write`, `admin`
and staff status alone are insufficient.

Global administrators also see all registered `NetworkNode` hosts, including
inactive entries, role, center, URL/HTTPS configuration status and last local
change. This exposes neither URLs nor secrets and performs no liveness probe;
remote storage telemetry is separately labeled.

Never put tokens, complete claims or clinical payloads in tickets, shell output or logs.

## Diagnosis

For an empty video list, check `video:read`, deployment role and a fresh login.
For denied playback, also check the processed artifact, `VideoState.anonymized`,
unset `processing_error`, and `meta.integrity_status` other than `lost`.

Inspect effective access without printing tokens:

```bash
devenv shell -- python manage.py shell -c \
  'from django.contrib.auth import get_user_model; from endoreg_db.services.centers.access import resolve_allowed_center_ids; from endoreg_db.services.hub import get_deployment_role; u=get_user_model().objects.get(username="BENUTZER"); print({"deployment_role": get_deployment_role(), "user_id": u.pk, "center_ids": sorted(resolve_allowed_center_ids(u) or []) if resolve_allowed_center_ids(u) is not None else "global"})'
```

| Structured event | Reason | Action |
| --- | --- | --- |
| `center_access_identity_sync_completed` | Cache replaced | Confirm expected user and center IDs. |
| `center_access_identity_sync_rejected` | `missing_groups_claim` | Configure the client mapper and renew login. |
| Same | `malformed_groups_claim` | Correct the claim shape. |
| Same | `unknown_center_keys` | Align Keycloak with deliberately deployed center data. |
| `center_access_denied` | `no_membership` | Assign membership unless the narrow hub read exception applies. |
| Same | `outside_center_scope` | Check the resource's center. |
| Same | `hub_video_not_anonymized_processed` | Check incomplete, failed or lost video state. |

Unknown claims never fall back to a default center.

## Terms and identity source

Access comes only from a cryptographically verified Keycloak `groups` claim:
a list of strings such as `/centers/berlin`. Nested paths, empty or unknown
`center_key` values fail authentication without changing local assignments.

`PortalUserInfo.centers` caches the claim and is fully replaced at each login.
Zero, one or multiple centers are valid. Default centers grant no access;
`is_staff` and `is_superuser` are explicit global exceptions.

### Keycloak claim delivery

Configure a Group Membership mapper named `groups`, with full paths enabled,
and assign its client scope to the application. Deliver groups in UserInfo and
the ID token for browser login, and the access token for bearer authentication.

Browser login verifies that UserInfo and ID-token subjects match. Explicit
UserInfo groups take precedence, including `[]` for revocation; an omitted claim
falls back only to the verified ID token. Bearer login uses verified access-token
groups. If neither trusted browser source supplies groups, login fails with
`missing_groups_claim`; missing mapper configuration does not revoke memberships.

After changing groups or mappers, sign out and in. Confirm
`center_access_identity_sync_completed` and the center IDs. Database membership
edits do not survive the next identity synchronization.

## Access matrix

“Own center” means verified membership, never a workflow default or deployment role.
The common rule applies to all four deployment roles unless noted.

| Resource or action | Common rule | Role-specific exception |
| --- | --- | --- |
| Video list and anonymization overview | Own centers | `central_hub` also lists other centers' anonymized processed videos with pseudonymous metadata. |
| Processed playback: HTTP Live Streaming (HLS), frames and timeline | Own centers and `video:read` | `central_hub` allows hub-wide reads only when processed, anonymized, error-free and not `lost`. |
| Raw video and frames | Own centers and `video:read` | None. |
| Patients and reports | Own centers and specialist role | `standalone` requires `patient:read` or `patient:write`. No hub exception. |
| Uploads | Own centers and `patient:write` | `local_study_server` requires exactly one declared own center. |
| Annotation exports | Own centers and `video:write`; no raw export | `local_study_server` requires exactly one own center or explicit global staff access. |
| Administration and quarantine | Applicable administrator or specialist role; target center boundary | `video:read` grants no administration rights. |
| Segment edits, reimport and export flag | Own center and applicable `*:write` role | No hub read exception. |
| Hub transfer receiver | Disabled (`404`) | Only `central_hub`: node credentials and mutual Transport Layer Security (mTLS); center from `NetworkNode.owning_center`, no Django session. |

Other-center hub detail responses exclude names, dates of birth, original
filenames, local paths, integrity errors, operator names and upload diagnostics.
The frontend displays `Video <id>` and the center.

### Technical enforcement and checkpoints

The backend enforces access; lx-annotate labels are not authorization.
`endoreg_db.views.access_control` separates hub read permission from strict
patient, raw-media and write checks. Foreign objects return `404` like missing
ones. Patient creation validates `center_key`; querysets restrict all other
operations. Exports compare video centers, optional `center_key` and memberships;
`all_centers` is an explicit staff exception.

Hub transfer authenticates nodes independently of user sessions. Anonymous
requests permitted by `EnvironmentAwarePermission` in local debug mode bypass
the downstream membership check only there, never in production.

`tests/views/test_center_access_matrix.py` and specialized views test all roles,
hub-transfer, FHIR, patient/report/raw-media boundaries and existence concealment.

## Migration, rollout, and rollback

Migration `0051_portaluserinfo_centers` creates plural memberships and copies
`Examiner.center`. Record the database backup, migration plan and existing
`PortalUserInfo` count before rollout.

1. Run `./feature-tracking/tracker.py validate` and the tracker's focused tests.
2. Test forward and reverse migration in a production-like environment; compare
   row counts and legacy access.
3. Set `ENDOREG_DEPLOYMENT_ROLE` explicitly; hub-wide reads require `central_hub`.
4. Restart all affected backends and force a new login.
5. Check a positive hub case and negative site-node, raw-media and write cases
   before recording operational evidence.

For unexpected visibility, roll back the application or remove the `central_hub`
role, then restart every backend. Memberships are not deleted automatically.
Database rollback restores the previous migration state; `Examiner.center`
remains during transition. Reverse migration removes new multiple assignments,
so first verify their export and restoration. Never export raw media or use it
as a recovery fallback.
