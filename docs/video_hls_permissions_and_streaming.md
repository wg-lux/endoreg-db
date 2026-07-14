# Video HLS Permissions and Streaming Contract

This document describes the permissions and streaming behavior implemented by
`endoreg-db` today. It is intentionally explicit about compatibility rules and
known gaps so that operators can distinguish an unavailable HLS artifact from
an authorization failure that is deliberately represented as `404`.

Local browser playback supports both raw and processed video as encrypted HLS.
Raw playback is strictly local, authenticated, RBAC-protected, and center
scoped. This does not change the hub boundary: raw clinical media remains
ineligible for outbound hub transfer.

## System boundaries

Three systems participate in browser playback:

1. **Keycloak and Django authentication** establish a local `request.user` and
   synchronize Keycloak roles into Django groups.
2. **endoreg-db** authorizes every playlist, content-key, and segment request,
   resolves a ready HLS artifact, unwraps keys, and selects an authorized file.
3. **Nginx** serves an already-authorized playlist or segment through an
   internal protected-media location. Nginx must not expose the HLS directory
   through a public alias.

The lx-annotate frontend is an authenticated HLS client. It is not an
authorization authority and cannot turn an unsuccessful backend response into
permission to read media.

## Permission evaluation order

An HLS request passes through independent gates in this order:

```text
request
  -> browser login middleware or Bearer-token authentication
  -> EnvironmentAwarePermission: authenticated in production
  -> PolicyPermission: route and method role
  -> VideoFile lookup
  -> explicit user-center versus video-center comparison
  -> DRF object-permission hook
  -> local HLS artifact-kind validation (raw or processed)
  -> READY artifact and protected-path validation
  -> authorized response or Nginx internal handoff
```

A request must pass every gate. Passing RBAC does not imply that the user may
read every center's videos, and a ready artifact does not override a center
denial.

### Response semantics by gate

| Gate | Typical failure | Meaning |
| --- | --- | --- |
| Browser authentication middleware | `302` to OIDC login | No authenticated browser session. Bearer clients are not redirected. |
| DRF authentication | `401` or `403`, depending on the active authentication class | Session or Bearer authentication did not establish an accepted user. |
| Route-role RBAC | `403` | Authenticated user does not satisfy the route's required role. |
| Video lookup | `404` | No `VideoFile` exists for that primary key. |
| Center scope | `404 {"detail":"Resource not found"}` | User has no usable center or the video belongs to a different/unresolved center. The response intentionally hides which case occurred. |
| Artifact kind | `404` | The requested kind is neither `raw` nor `processed`. |
| Artifact/path lookup | `404` with HLS-unavailable detail | No ready artifact of the requested kind exists or its playlist/segment path is inconsistent. |
| Nginx segment offload | `404` | Protected segment offload is disabled or the authorized path cannot be served. |

The frontend currently maps every playlist `404` to “Encrypted HLS playback is
not available for this video yet.” That message is safe from a resource
enumeration perspective but is operationally ambiguous: it can mean center
denial or genuine artifact unavailability.

## Authentication

### Browser session flow

Unauthenticated browser requests to protected paths are redirected to the OIDC
login URL with a relative `next` target. After Keycloak login, the OIDC backend:

- verifies the identity-provider response through the OIDC library
- creates or updates the local Django user
- extracts flat `roles` and `realm_access.roles` claims
- creates matching Django groups where necessary
- replaces the user's group memberships with the current Keycloak role set

The browser subsequently authenticates API and HLS GET requests with the
Django session cookie. HLS.js requests use `withCredentials=true`.

### Bearer-token flow

API clients can send `Authorization: Bearer <token>`. The JWT authentication
class:

- obtains the Keycloak signing key through JWKS
- verifies RS256 signature, issuer, audience, expiry, issued-at, and required
  claims
- creates or locates the Django user
- synchronizes token roles into Django groups when roles are present

Production forces TLS verification for OIDC/JWKS communication.

### CSRF

The lx-annotate Axios client reads the CSRF cookie and sends
`X-CSRFToken` on API requests. HLS resources are GET-only, so CSRF is not the
authorization mechanism for playback. Session authentication, RBAC, and center
scope remain mandatory. Session cookies and CSRF values must never be printed
to browser or server logs.

## Route-role RBAC

The HLS routes map to the `video` resource:

- `video-hls-playlist-m3u8`
- `video-hls-playlist`
- `video-hls-key`
- `video-hls-segment`
- the legacy `video-stream` and `video-detail-stream` routes

GET, HEAD, and OPTIONS require `video:read`; mutating video routes normally
require `video:write`.

Roles are evaluated from Django groups synchronized from Keycloak. The current
role satisfaction rules are:

1. An exact required role allows access.
2. `<resource>:write` satisfies `<resource>:read`.
3. `data:read` or `data:write` satisfies resource read roles.
4. `data:write` satisfies resource write roles.
5. `endoregdb_user` currently satisfies every mapped role.

Rules 3 through 5 are compatibility behavior and make route RBAC broader than
a strict per-resource model. They should be included in access reviews and not
mistaken for least privilege.

Unmapped production routes fall back by method to `data:read` or `data:write`.
If no role can be resolved, the policy denies access. Debug and pytest modes
bypass the production RBAC behavior and must not be used as evidence of
production access.

## Center-scoped object access

Route RBAC answers “may this identity use video-read APIs?” Center scope
answers “may this identity read this particular center's video?”

### User center resolution

For a normal authenticated user, the allowed center is resolved through:

```text
auth.User
  -> PortalUserInfo
    -> Examiner
      -> center_id
```

The OIDC backends synchronize identity fields and roles, but do not trust a
login claim as a clinical center assignment. A global administrator creates or
updates the local association through the Access Management workflow.

The resolver returns:

- `None` for staff or superusers, meaning no center restriction
- a positive center ID for a correctly linked examiner
- `-1` for an authenticated non-privileged user without a complete
  `PortalUserInfo → Examiner → Center` association
- `None` for anonymous users, although production authentication should reject
  them before this stage

An authenticated normal user returning `-1` is denied with a masked `404`.

### Video center resolution

The generic object-center resolver checks, in order:

- direct `center_id` or `source_center_id`
- `center.id`
- `patient.center_id`
- `patient_examination.patient.center_id`
- `sensitive_meta.center_id`
- nested `video.center_id`, `pdf.center_id`, or upload-job source center

For HLS the object is the `VideoFile`. If no center can be resolved, access is
denied for a center-restricted user. The guard never falls back to the runtime
default center merely to make playback succeed.

### Why center failures use 404

Returning the same generic response for an unknown video, an unassigned user,
and a cross-center video reduces resource enumeration. A user should not be
able to determine that another center owns video ID 6 by comparing permission
responses.

This privacy property also means the browser cannot tell the operator how to
repair provisioning. Server-side diagnostics must compare the user's examiner
center with the video's resolved center without logging patient metadata.

### Provisioning invariant

Before granting a non-staff clinical user access to video workflows, operators
must ensure:

```text
user.portaluserinfo exists
user.portaluserinfo.examiner exists
user.portaluserinfo.examiner.center_id equals the intended Center.id
```

Keycloak role assignment alone is insufficient. A user can pass
`video:read` RBAC and still receive `404 Resource not found` for every HLS
request if the local clinical association is absent.

## Administrative management of user center scope

### Supported Access Management workflow

LX-Annotate provides a protected Administration page backed by dedicated
center-scope API routes. It lists locally known OIDC/Django users, their
Keycloak-synchronized roles, assignment state, and available centers. The
Application Settings center selector remains unrelated: it changes workflow
defaults, not `user.portaluserinfo.examiner.center_id`.

An assignment requires the exact synchronized `center_scope:admin` group. The
ordinary compatibility role, `data:write`, and `video:write` are not accepted
as substitutes. Keycloak remains the source of truth for technical roles; the
administration API never creates users or changes Keycloak groups.

A global Django superuser may assign an existing user whose local clinical
relationship is incomplete. In the same database transaction the service:

1. creates a `PortalUserInfo` when absent
2. creates a non-real-person, pseudonymous `Examiner` when absent
3. assigns the examiner to the selected center
4. persists an immutable audit-ledger entry

No patient demographics or identity-provider secrets are manufactured or
logged. If the audit write fails, all provisioning and assignment changes roll
back. A delegated center administrator may change existing relationships only
inside their own center and may not provision an incomplete relationship,
move users across centers, or change their own scope.

The first strictly authorized administrator can be promoted with the dedicated
deployment command after the account has logged in through Keycloak and has the
exact synchronized `center_scope:admin` group:

```bash
python manage.py bootstrap_center_admin --username lx_bootstrap_admin
```

The command never creates a user or group, never assigns a Keycloak role, and
does not accept the broad `endoregdb_user` compatibility role. It locks the
existing user, verifies the exact group, sets `is_staff` and `is_superuser` in a
transaction, and requires a persisted immutable audit-ledger entry. If the
audit write is unavailable, the promotion rolls back. Repeating the command for
an already promoted user makes no further change or duplicate audit entry.

The command must be executed inside the deployed application environment by an
operator or controlled release job. Adding it to source code does not modify a
deployed database, and it must not be exposed as a public bootstrap endpoint or
automatic login side effect.

### Effect of a current assignment change

The center resolver reads the local relationship on every protected request;
the allowed center is not copied into the HLS artifact or trusted from a
frontend field. Once the database relationship changes, subsequent playlist,
key, and segment requests use the new scope without requiring HLS
rematerialization.

Revocation cannot retract encrypted bytes or decrypted frames already delivered
to the browser, but future protected requests are denied. The player therefore
must continue using authenticated backend URLs for every HLS resource rather
than receiving public or long-lived bearer URLs.

### Administration contract

The backend and frontend provide:

- a paginated list of locally known OIDC users
- each user's current center-assignment status: assigned, incomplete, or
  unassigned
- center choices identified by immutable `center_key`, with display names kept
  separate
- an explicit assignment operation
- an explicit revocation operation
- conflict-safe updates so two administrators cannot silently overwrite each
  other
- a durable audit record containing actor, target user, previous center, new
  center, timestamp, reason, and request/correlation ID
- no patient metadata, tokens, cookies, or secrets in the audit payload

The frontend lives under a protected Administration page, requires a reason
and confirmation for mutations, and displays incomplete relationships
explicitly.

The API routes are:

```text
GET  /api/administration/center-scopes/
POST /api/administration/center-scopes/<user-id>/
```

Assignments include `center_key`, `expected_center_key`, and a human reason.
Revocations omit `center_key`. The expected value provides optimistic conflict
detection, and `X-Request-ID` is retained as audit correlation when supplied.

### Authorization requirements for that surface

Center administration must use a stricter permission than ordinary
`data:write`, `video:write`, or `endoregdb_user`.

The current `satisfies()` compatibility behavior makes `endoregdb_user` pass
every normal route-role check. Merely adding a route mapped to `admin` would
therefore not create a strict administrative boundary if it continued using
that helper unchanged. A center-assignment endpoint must use an explicit
administrative permission that:

- requires an exact dedicated role such as `center_scope:admin`, or a locally
  controlled staff/superuser decision
- does not accept the `endoregdb_user` global compatibility override
- does not accept `data:write` as an administrative substitute
- prevents a center-scoped administrator from assigning users outside the
  administrator's delegated centers, if delegated administration is enabled
- prevents self-escalation unless separately authorized and audited
- fails closed when the target user, examiner, or center identity is ambiguous

Keycloak should remain the source of truth for technical roles. The local
clinical center assignment should remain an explicit application record unless
a reviewed, immutable Keycloak center claim and synchronization lifecycle are
introduced.

### Data-model limitation

The current model derives access scope from an `Examiner` relationship. This is
adequate only when every application user has one center. The provisioning
workflow intentionally creates a pseudonymous, non-real-person examiner solely
to preserve this established scope invariant.

If non-examiner users, support personnel, or multi-center users are required,
introduce a dedicated user-center membership model rather than manufacturing
fake `Examiner` records. Such a model should make cardinality, delegated roles,
validity dates, revocation, and audit provenance explicit. The HLS center
resolver should then consume that single canonical membership service.

If multi-center membership or time-bounded delegation is introduced, replace
this single-center relation with a dedicated membership model and update the
central resolver rather than adding HLS-specific exceptions.

## HLS route behavior

### Canonical endpoints

```text
GET /endoreg-api/media/videos/<id>/hls/playlist.m3u8?type=<raw|processed>
GET /endoreg-api/media/videos/<id>/hls/playlist/?type=<raw|processed>
GET /endoreg-api/media/videos/<id>/hls/key/<key-id>/
GET /endoreg-api/media/videos/<id>/hls/segments/<key-id>/<segment-name>
```

The slash and `.m3u8` playlist routes invoke the same view.

### Compatibility endpoints

```text
GET /endoreg-api/media/videos/<id>/stream/
GET /endoreg-api/media/videos/<id>/
```

These endpoints do not stream MP4. They return `302` to the canonical
processed HLS playlist and add:

```text
X-Stream-State: hls_compat_redirect
Link: <...playlist...>; rel="alternate"; type="application/vnd.apple.mpegurl"
```

The compatibility view ignores legacy `type=raw` intent and always redirects
to processed HLS.

### Playlist request

After permission and center checks, the playlist view:

1. accepts the local `raw` or `processed` artifact kind
2. selects a `VideoHlsArtifact` for the video with `status=ready`
3. verifies that the protected playlist and segment directory exist and that
   segment count is positive
4. resolves the playlist strictly below `PROTECTED_MEDIA_ROOT`
5. returns the playlist directly only when Nginx offload is disabled, otherwise
   issues an internal Nginx handoff

The response MIME type is `application/vnd.apple.mpegurl`. Playlist responses
are `private, no-store`, receive `X-Content-Type-Options: nosniff`, and only
receive CORS headers for a configured frontend origin.

### Content-key request

The playlist contains a key URL bound to both the video ID and a UUID key ID.
The key view repeats authentication, RBAC, center scope, and object permission
checks. It then:

1. selects a ready artifact matching the requested video and key ID
2. confirms the artifact belongs to the requested video
3. verifies artifact paths
4. unwraps the 16-byte HLS content key using the application master key and
   artifact-bound authenticated data
5. returns it as `application/octet-stream`

The plaintext content key is not stored in the database. Key responses are
`private, no-store` and are never delegated to a public Nginx path.

### Segment request

Each segment request repeats authentication, RBAC, center scope, and artifact
binding. Segment names must be a basename matching `seg_*.ts`; slashes,
backslashes, traversal, and other suffixes are rejected. The resolved file must
remain below the artifact segment directory and protected media root.

Segments require configured Nginx offload. Django returns an internal
`X-Accel-Redirect` only after authorization; Nginx then reads the protected
file. Segment responses use `video/mp2t` and
`private, max-age=31536000, immutable`. The long cache lifetime is safe for
artifact identity because key IDs and versioned directories change on
replacement, and the cached segment remains AES-128 encrypted.

## Artifact materialization and readiness

An HLS artifact is identified by video and artifact kind and moves through:

```text
materializing -> ready
materializing -> failed
```

Queue dispatch result `queued` is not a database artifact status. It only means
a Celery task was accepted. Likewise, a systemd dispatcher exiting successfully
does not mean the task completed.

For new imports, both raw and processed HLS readiness are part of successful
import finalization. A transcode failure propagates so the import stays
retryable instead of publishing a partially streamable clinical record. Legacy
videos require separate raw and processed backfill jobs.

The playback API serves only `ready` artifacts whose files are consistent.
Materializing, failed, missing, and path-inconsistent artifacts all fail closed.

## Frontend streaming lifecycle

The lx-annotate authenticated stream composable performs this sequence:

```text
video ID / raw or processed kind
  -> build same-origin playlist URL
  -> credentialed Axios playlist preflight
  -> require HLS MIME type and #EXTM3U signature
  -> attach Hls.js where Media Source Extensions are available
     or credentialed native HLS where supported
  -> credentialed same-origin key and segment XHRs
  -> bounded buffering and one media-error recovery attempt
  -> abort requests, destroy Hls.js, and clear the media element on teardown
```

The player rejects cross-origin media URLs, does not silently fall back to
progressive plaintext video, and clears the source to release buffered decrypted
media when the component changes or unmounts.

A browser `DOMException` stating that fetching was aborted can be a teardown
effect after a fatal playlist response: the composable aborts pending work and
calls `video.load()` after removing the source. It is not proof that the network
abort caused the original HLS failure.

## Operational diagnosis

Use the response body and server-side state, not only the frontend message:

| Observation | Interpretation |
| --- | --- |
| RBAC log says `DENY` | Role mapping failed; center and artifact code were not authorized to proceed. |
| RBAC says `ALLOW`, response is `404 Resource not found` | Check local user-to-examiner-to-center provisioning and the video's center. |
| Response says HLS playlist unavailable | Check `VideoHlsArtifact` state and protected paths. |
| Worker says `already_ready` but browser gets generic resource-not-found | Artifact is likely healthy; investigate center scope before retranscoding. |
| `200 application/vnd.apple.mpegurl`, then key failure | Investigate center/key binding and master-key availability. |
| Playlist and key succeed, segment is `404` | Investigate protected Nginx offload and segment path. |
| Browser reports unsupported Apple HLS MIME on `/stream/` | An old frontend assigned the redirect target directly to `<video src>` instead of using Hls.js. |

Do not repair a center denial by forcing HLS regeneration. Transcoding cannot
change user authorization.

## Current gaps and hardening backlog

1. **Center provisioning is intentionally separate from OIDC role sync.** Login
   does not silently grant a clinical scope. Administrators must explicitly and
   auditably assign or revoke it in Access Management.
2. **Broad compatibility roles remain active.** `endoregdb_user` passes all
   route-role checks, and `data:*` roles satisfy resource roles. Center scope is
   therefore a critical second boundary.
3. **The object-permission hook is not a separate video ACL.** The HLS views call
   `check_object_permissions()`, but the active permission classes add
   authentication/RBAC rather than per-video grants. The explicit center guard
   is the effective object-level control.
4. **The legacy redirect checks center only at the destination.** The
   compatibility `/stream/` view looks up the video and redirects without first
   calling the center guard. A cross-center caller with video RBAC can therefore
   distinguish an existing video (`302`) from a nonexistent ID (`404`) before
   the destination playlist correctly denies access. The redirect view should
   apply the same center check.
5. **Frontend `404` language is deliberately non-diagnostic.** Privacy masking prevents a
   detailed client error, but “not available yet” incorrectly implies missing
   materialization. Operator-facing correlation or a privileged diagnostic
   surface is needed without weakening the public response.
6. **Single-center examiner scope is a model constraint.** Multi-center users
   require a dedicated membership model, not exceptions in media views.

These gaps must be tracked as permissions and workflow issues. They do not
justify exposing the protected media tree, removing center checks, disabling
certificate validation, or adding a progressive plaintext fallback.

## Source ownership

| Concern | Source |
| --- | --- |
| OIDC user and role synchronization | `endoreg_db/authz/backends.py` |
| Bearer JWT verification | `endoreg_db/authz/auth.py` |
| Production authentication requirement | `endoreg_db/utils/permissions.py` |
| Route-to-role mapping and compatibility roles | `endoreg_db/authz/policy.py` |
| RBAC enforcement | `endoreg_db/authz/permissions.py` |
| User and object center resolution | `endoreg_db/services/hub/ingest.py`, `endoreg_db/views/access_control.py` |
| Clinical user-to-center association | `endoreg_db/models/administration/person/user/portal_user_information.py` and `Examiner` |
| HLS playlist, key, and segment views | `endoreg_db/views/video/hls_stream.py` |
| Legacy redirects | `endoreg_db/views/video/video_stream.py` |
| Artifact state, key wrapping, path validation, and FFmpeg | `endoreg_db/services/hls_media.py`, `VideoHlsArtifact` |
| Browser playback | lx-annotate `useAuthenticatedVideoStream.ts` |

Any change to one layer must be tested with the remaining layers enabled. A
route-level unit test or successful FFmpeg task alone is not proof that an
ordinary center-scoped production user can stream the video.
