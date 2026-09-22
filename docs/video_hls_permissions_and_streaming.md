# Video HTTP Live Streaming Permissions and Streaming Contract

This document describes the current `endoreg-db` authorization and streaming
implementation. Production-readiness status remains authoritative only in
[`VideoStorageNormalization.yml`](../feature-tracking/VideoStorageNormalization.yml).
In particular, a passing unit test or a `queued` task result is not evidence
that the complete production playback workflow is ready.

HTTP Live Streaming (HLS) supports encrypted raw and processed playback. Raw
playback is local, authenticated, role-protected, and center-scoped. Raw media
remains ineligible for outbound hub transfer.

## System Boundaries

1. Keycloak and Django authentication establish `request.user`, synchronize
   technical realm roles into Django groups, and synchronize validated center
   group paths into `PortalUserInfo.centers`.
2. `endoreg-db` authorizes every playlist, content-key, and segment request,
   resolves an eligible HLS artifact, unwraps its content key, and selects the
   protected file.
3. Nginx serves an already-authorized playlist or segment through an internal
   protected-media location. The HLS directory must never be exposed through a
   public alias.

The lx-annotate frontend is an authenticated HLS client, not an authorization
authority.

## Authentication and Role-Based Access Control

Browser sessions use OpenID Connect (OIDC). Bearer clients use Keycloak JSON
Web Tokens (JWTs) whose signature, issuer, audience, expiry, issue time, and
required claims are validated. Production verifies Transport Layer Security
(TLS) for OIDC and JSON Web Key Set communication.

Realm roles are accepted from the flat `roles` claim and
`realm_access.roles`. The current compatibility rules remain broad:

- an exact required role grants access;
- `<resource>:write` satisfies `<resource>:read`;
- `data:read` and `data:write` satisfy resource read roles;
- `data:write` satisfies resource write roles;
- `endoregdb_user` satisfies every ordinary mapped route role.

The frontend still requires `endoregdb_user` for global application routing.
This is not least privilege. Resource roles such as `video:read` remain useful
for migration visibility but do not narrow an identity that also has
`endoregdb_user`.

HLS GET, HEAD, and OPTIONS routes are mapped to the `video` resource and
require `video:read` after compatibility-role evaluation. Debug and pytest
modes bypass parts of production authorization and are not production-access
evidence.

## Center Membership

Center scope is plural. Keycloak group paths of the exact form
`/centers/<center_key>` are validated during login and replace the local
`PortalUserInfo.centers` cache. A malformed `groups` claim, nested or empty
center key, or unknown `center_key` fails synchronization. Arbitrary Keycloak
groups do not grant center access.

`resolve_allowed_center_ids()` returns:

- `None` for staff or superusers, meaning unrestricted local center scope;
- a set of zero, one, or several center identifiers for normal users.

During the migration, a legacy `PortalUserInfo.examiner.center` is added to the
resolved set when present. New authorization logic must use the plural resolver;
`resolve_allowed_center_id()` is only a compatibility adapter and raises when a
user has multiple centers.

The application does not fall back to the deployment's default center. A
normal authenticated user with no resolved membership is denied. Cross-center
denials use a masked `404` response to reduce resource enumeration.

On a central-hub deployment, authenticated read-only access to an anonymized,
processed, non-failed, non-lost video may cross center boundaries. This narrow
exception does not permit raw-media access or mutation.

The legacy processed-stream redirect applies the same anonymized center-scope
guard before returning its redirect; it no longer exposes video existence to a
cross-center caller merely through a `302` response.

## Request Evaluation Order

An HLS request passes these independent gates:

```text
request
  -> browser session or Bearer-token authentication
  -> EnvironmentAwarePermission
  -> PolicyPermission
  -> CenterScopedVideoPermission
  -> VideoFile lookup
  -> raw or processed artifact-kind check
  -> artifact readiness and current-source identity checks
  -> protected-path validation
  -> authorized Django response or internal Nginx handoff
```

Passing a role check does not grant access to every center, and an HLS artifact
does not override a center denial.

## Canonical Routes

The canonical application prefix is `/endoreg-api/`. `/api/` remains a
temporary compatibility mount for older clients.

```text
GET /endoreg-api/media/videos/<id>/hls/playlist.m3u8?type=<raw|processed>
GET /endoreg-api/media/videos/<id>/hls/playlist/?type=<raw|processed>
GET /endoreg-api/media/videos/<id>/hls/key/<key-id>/
GET /endoreg-api/media/videos/<id>/hls/segments/<key-id>/<segment-name>
```

The compatibility routes
`/endoreg-api/media/videos/<id>/stream/` and
`/endoreg-api/media/videos/<id>/` return a `302` redirect to the canonical
processed playlist. They do not stream a plaintext MPEG-4 Part 14 (MP4) file
and ignore legacy `type=raw` intent.

## Playlist, Key, and Segment Behavior

The playlist endpoint accepts only `raw` or `processed`, applies the
authorization gates, and looks up a ready artifact. Missing, failed,
materializing, stale-identity, and path-inconsistent artifacts fail closed. A
missing playlist can reserve bounded asynchronous materialization and return a
private, non-cacheable `202 Accepted` response with `Retry-After`; dispatch
failure returns `503 Service Unavailable`. The web process does not transcode.

Successful playlist responses use
`application/vnd.apple.mpegurl`, `private, no-store`, and
`X-Content-Type-Options: nosniff`. Cross-origin headers are emitted only for a
configured frontend origin.

The content-key endpoint binds a Universally Unique Identifier (UUID) key to
the requested video and artifact, repeats authorization, and unwraps the
16-byte content key using artifact-bound authenticated data. Plaintext keys are
not stored in the database and key responses are never delegated to a public
Nginx path.

Segment names must be a basename matching `seg_*.ts`. Traversal, alternate
suffixes, and paths outside the artifact directory or protected-media root are
rejected. Segment delivery requires configured internal Nginx offload. The
response uses `video/mp2t` and can be cached immutably because the segment is
encrypted and the key identifier and generation directory change on
replacement.

Playlist, key, and segment requests renew a media-operation lease. Publication,
regeneration, and cleanup must defer while the relevant lease is active.

## Artifact Materialization

The durable artifact states are:

```text
queued -> materializing -> ready
                       \-> failed
```

`queued` as a dispatch result means only that the broker accepted a task. It
does not mean that a ready artifact exists. Stale materialization is reconciled
against the configured policy before new work is reserved.

The operator command defaults to a dry-run audit and selects raw and processed
artifacts unless explicitly narrowed:

```bash
python manage.py materialize_video_hls
python manage.py materialize_video_hls --apply
```

Legacy ready rows without `source_content_hash` are rejected by materializer,
playlist, key, and segment lookups. Enabled LuxNix hosts can dispatch automatic
replacement work, but repository state does not establish complete-corpus
production convergence.

## Current Production-Readiness Limits

The implementation is not yet a complete production-readiness proof. The
feature tracker currently records these material limits:

- import and correction callers do not consistently enforce the real typed
  terminal HLS result, so a caller can report false success or reject a
  successful publication;
- backfill admission and accounting can select a populated file field whose
  physical source is absent, and its outcome counts are not yet reliable per
  distinct video;
- persisted and emitted HLS errors can still include source-path context, and
  health reporting and recovery do not yet use one stale-work policy;
- failure-injection evidence is missing for broker loss, hard worker loss,
  multi-process concurrency, and the packaged lx-annotate production runtime;
- source-generation and publication integrity are implemented in part, but
  formal production rollout and corpus-convergence evidence remain outstanding.

Do not describe imports as fully stream-ready merely because HLS work was
dispatched. Do not enable destructive cleanup based on HLS state until the
tracker's required gates are verified.

## Frontend Lifecycle and Diagnosis

The lx-annotate stream client performs a credentialed playlist preflight,
checks the HLS Multipurpose Internet Mail Extensions type and `#EXTM3U`
signature, uses Hls.js where Media Source Extensions are available, and tears
down requests and buffered media when the component changes or unmounts. It
does not fall back to progressive plaintext playback.

The frontend intentionally maps playlist `404` responses to a non-diagnostic
message. Operators must distinguish authorization from artifact state using
server-side evidence:

| Observation | Operator interpretation |
| --- | --- |
| Role log records `DENY` | Technical role mapping failed. |
| `404 Resource not found` after role acceptance | Check center membership and the hub processed-media exception. |
| HLS unavailable response | Check artifact state, current-source identity, and protected paths. |
| Playlist succeeds but key fails | Check key binding and master-key availability. |
| Playlist and key succeed but a segment is `404` | Check internal Nginx offload and the protected segment path. |

Never repair an authorization failure by retranscoding and never expose the
protected media tree, disable certificate validation, or add a plaintext
fallback.

## Source Ownership

| Concern | Source |
| --- | --- |
| OIDC user, role, and center synchronization | `endoreg_db/authz/backends.py`, `endoreg_db/authz/auth.py` |
| Center-group validation and plural membership | `endoreg_db/services/center_access.py` |
| Route policy and compatibility roles | `endoreg_db/authz/policy.py`, `endoreg_db/authz/permissions.py` |
| Object and hub center checks | `endoreg_db/views/access_control.py` |
| HLS playlist, key, and segment views | `endoreg_db/views/video/hls_stream.py` |
| Compatibility redirect | `endoreg_db/views/video/video_stream.py` |
| Artifact lifecycle and transcoding | `endoreg_db/services/hls_media.py`, `VideoHlsArtifact` |
| Browser playback | lx-annotate `useAuthenticatedVideoStream.ts` |

Changes to one layer require verification with all remaining layers enabled.
A route-level test or successful FFmpeg task alone does not prove that an
ordinary production user can stream the video.
