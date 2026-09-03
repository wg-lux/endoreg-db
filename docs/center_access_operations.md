# Center access and central-hub video visibility

This runbook describes the operational contract for the
[`center_access`](../feature-tracking/CenterAccess.yml) feature.

## Repository and deployment status

The repository implements and tests the access matrix, identity-backed center
memberships, central-hub processed-video reads, and administration surfaces
described below. The feature tracker remains `active`: its manual security
review, complete access-regression assessment, production-like migration and
rollout exercise, alerts, and administrator/operator exercise remain
`in_progress`. Repository evidence therefore does not establish that a specific
production deployment has enabled or operationally approved these capabilities.

## Terms and identity source

Center access is derived exclusively from an already cryptographically verified
Keycloak `groups` claim. A center uses the group format
`/centers/<center_key>`, for example `/centers/berlin`. The claim must be a list
of strings. Nested paths, empty keys, and unknown `center_key` values are
rejected. A failed login does not change the existing local assignment.

The local many-to-many `PortalUserInfo.centers` relationship is only a cache of
the identity source. At every new login, the verified claim completely replaces
the cache. Zero, one, or multiple centers are valid. The workflow's configured
default center grants no permission. `is_staff` and `is_superuser` remain
explicit global exceptions; missing memberships never imply global access.

## Access matrix

In this table, “own” always means assigned to a center through verified
membership. A default center or deployment role does not replace that
assignment.

| Resource/action | `standalone` | `site_node` | `local_study_server` | `central_hub` |
|---|---|---|---|---|
| Video list and anonymization overview | own centers | own centers | own centers | complete for own centers; other centers only for anonymized, processed videos with pseudonymous metadata |
| Anonymized, processed playback: HTTP Live Streaming (HLS) playlist, key, segment, frame, and timeline | own centers and `video:read` | own centers and `video:read` | own centers and `video:read` | hub-wide with `video:read` when processed, anonymized, error-free, and not `lost` |
| Raw video and raw frames | own centers and `video:read` | own centers and `video:read` | own centers and `video:read` | own centers and `video:read`; no hub exception |
| Patients | own centers and `patient:read` or `patient:write` | own centers and specialist role | own centers and specialist role | own centers and specialist role; no hub exception |
| Reports | own centers and `patient:read` or `patient:write` | own centers and specialist role | own centers and specialist role | own centers and specialist role; no hub exception |
| Uploads | own centers and `patient:write` | own centers and `patient:write` | exactly one declared own center and `patient:write` | own centers and `patient:write`; no hub exception |
| Annotation exports | own centers and `video:write` | own centers and `video:write` | exactly one own center, or explicit global staff access, and `video:write` | own centers and `video:write`; raw-media export remains prohibited |
| Administration and quarantine | only the applicable administrator or specialist role; the target resource's center boundary remains in force | same | same | same; `video:read` grants no administration access |
| Write operations, including segment changes, reimport, and export flag | own center and applicable `*:write` role | own center and applicable `*:write` role | own center and applicable `*:write` role | own center and applicable `*:write` role; no hub read exception |
| Hub transfer receiver: registration, status, and processed medium | disabled (`404`) | disabled (`404`) | disabled (`404`) | valid node credentials and mutual Transport Layer Security (mTLS); center exclusively from `NetworkNode.owning_center`, without a Django user session |

A hub detail response for another center excludes patient names, dates of birth,
original filenames, local paths, integrity errors, operator names, and upload
diagnostics. The lx-annotate frontend displays the neutral label `Video <id>`
and the center.

### Technical enforcement and checkpoints

- `endoreg_db.views.access_control` separates the narrow read-only hub exception
  from strict center checks for patients, raw media, and URL-addressed video
  write paths. Existing objects from another center return `404`, just like
  missing objects, to avoid a resource-existence oracle.
- The documented debug contract remains consistent: when
  `EnvironmentAwarePermission` permits anonymous local debug requests, the
  downstream center check does not impose a contradictory membership block.
  This bypass does not apply in production.
- Hub transfer endpoints are a separate machine-to-machine boundary. They
  authenticate the `NetworkNode` and bind every operation to its
  `owning_center`; Django user roles and memberships are neither required nor
  evaluated there.
- `PatientViewSet.get_queryset()` restricts list, detail, update, and deletion
  to memberships; creation checks the validated `center_key` before saving.
- Upload and annotation export require a specialist role. The export service
  also compares video centers, an optional `center_key`, and effective
  memberships. `all_centers` remains an explicit staff exception.
- Segment changes, corrections, reimport, and export approval use both
  `video:write` and the strict center check. These paths do not invoke the hub
  playback exception.
- lx-annotate consumes center keys and pseudonymous labels from the application
  programming interface (API), but makes no security decision; authorization
  remains in the backend.
- `tests/views/test_center_access_matrix.py` covers patient boundaries for all
  four roles and negative hub cases for upload, export, administration, and
  write access. Specialized view tests cover hub transfer, Fast Healthcare
  Interoperability Resources (FHIR), patient creation, reports, raw media,
  existence concealment, and processed playback.

## Assignment, revocation, and refresh

The administration page manages the local plural `PortalUserInfo.centers`
assignment. Django superusers may manage all users and centers. The
Keycloak-synchronized `center_scope:global_admin` role grants the same global
center administration without making the user a Django superuser.
`center_scope:admin` remains limited to the user's single, unambiguous own
center. Broad roles such as `data:write` or `admin`, and staff status alone, are
insufficient.

Both administrator permissions are assigned as exactly named **Keycloak realm
roles**. The global administration view uses `center_scope:global_admin`, and
delegated center administration uses `center_scope:admin`. The application
synchronizes these roles into same-named Django groups at login, but does not
assign or revoke Keycloak roles itself.

Global administrators also see every host registered in `NetworkNode` on the
administration page, including inactive entries, role, center, URL/HTTPS
configuration status, and time of the last local change. The view exposes
neither the URL nor shared secret and performs no remote liveness probe when
opened. It deliberately shows local registration and configuration state;
runtime telemetry from storage nodes remains separately labeled.

Every change requires a reason and conflict protection based on the center keys
read previously, and is durably audited. Changes to the caller's own account are
prohibited. The API does not change Keycloak roles or groups: at the next login,
the verified `/centers/<center_key>` claim replaces the local cache. Persistent
assignments must therefore also be made in Keycloak.

1. In the identity provider, assign or revoke the `/centers/<center_key>`
   groups. The key must already exist in `Center.center_key`.
2. End the active session and log in again, or fully renew the OpenID Connect
   authentication flow. Reloading the page does not update an already issued
   token.
3. Confirm that `center_access_identity_sync_completed` was logged for the
   expected user ID and center IDs.
4. After revocation, also confirm that resources outside the remaining centers
   return HTTP `404`.

Never copy tokens, complete claims, or clinical payloads into tickets, shell
output, or logs.

## Diagnosis

Inspect the effective configuration without exposing token contents:

```bash
devenv shell -- python manage.py shell -c \
  'from django.contrib.auth import get_user_model; from endoreg_db.services.center_access import resolve_allowed_center_ids; from endoreg_db.services.hub import get_deployment_role; u=get_user_model().objects.get(username="BENUTZER"); print({"deployment_role": get_deployment_role(), "user_id": u.pk, "center_ids": sorted(resolve_allowed_center_ids(u) or []) if resolve_allowed_center_ids(u) is not None else "global"})'
```

Relevant structured JavaScript Object Notation (JSON) events:

- `center_access_identity_sync_completed`: membership cache was replaced.
- `center_access_identity_sync_rejected` with `malformed_groups_claim`: claim
  shape is invalid.
- `center_access_identity_sync_rejected` with `unknown_center_keys`: identity
  provider and center master data disagree.
- `center_access_denied` with `no_membership`: no assignment exists outside the
  hub read exception.
- `center_access_denied` with `outside_center_scope`: resource belongs to another
  center.
- `center_access_denied` with `hub_video_not_anonymized_processed`: a hub request
  targeted an incomplete, failed, or lost video.

For an empty video list, first check `video:read`, the deployment role, and a
fresh login. For denied playback, also confirm that a processed artifact exists,
`VideoState.anonymized` is set, `processing_error` is not set, and
`meta.integrity_status` is not `lost`. Correct unknown center claims in Keycloak
or in deliberately deployed center master data; there is no automatic fallback
to the default center.

## Migration, rollout, and rollback

Migration `0051_portaluserinfo_centers` creates plural memberships and copies
existing `Examiner.center` assignments forward. Before rollout, record the
database backup, migration plan, and count of existing `PortalUserInfo` rows.
Rollback uses the previous application version and migrates Django to the
previous state; the old `Examiner.center` relationship remains during the
transition. The reverse migration removes new multiple assignments and must run
only after a verified export and restore check.

Rollout sequence:

1. Run `./feature-tracking/tracker.py validate` and the focused tests from the
   tracker.
2. Test the migration forward and backward in a production-like environment;
   compare row counts and legacy access.
3. Check `ENDOREG_DEPLOYMENT_ROLE` explicitly. Hub-wide visibility is permitted
   only for `central_hub`.
4. Restart all affected backend processes so settings and code are loaded
   consistently, then force a new login.
5. Check one positive hub case and one negative site-node, raw-media, and write
   case. Only then update the operational assessment in the tracker.

If unexpected visibility occurs, first roll back the application version or
remove the `central_hub` deployment role, then restart every backend process.
Membership data is not deleted automatically. Raw media is neither exported nor
used as a recovery fallback.
