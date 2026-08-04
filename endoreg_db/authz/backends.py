# endoreg_db/authz/backends.py
#
# Purpose:
#   Authenticate browser users via OIDC (Keycloak), create/update a Django User on first login,
#   and sync Keycloak *realm roles* into Django Groups so DRF permissions can check them.
#
# Flow:
#   /oidc/authenticate/ → Keycloak login → /oidc/callback/ → this backend verifies ID token
#   and returns a Django User instance. We then:
#     - create the user on first login (create_user)
#     - update profile fields on later logins (update_user)
#     - sync roles → Django groups (so request.user.groups contains "data:read", etc.)
#
# Notes:
#   - We assume you added a Keycloak mapper so roles appear either as a flat "roles" claim,
#     or the standard "realm_access": {"roles": [...]}.
#   - We *replace* the user's groups each login to match Keycloak (source of truth).
#   - If you also need *client roles* (per-client), see the optional code in _extract_realm_roles().
#
# Settings that enable this backend (in config/settings/dev.py):
#   AUTHENTICATION_BACKENDS = (
#       "endoreg_db.authz.backends.KeycloakOIDCBackend",
#       "django.contrib.auth.backends.ModelBackend",
#   )

from mozilla_django_oidc.auth import OIDCAuthenticationBackend
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group

User = get_user_model()


def _extract_realm_roles(claims):
    """
    Extract Keycloak *realm* roles from ID token claims.

    We support two common forms:
      1) A custom 'roles' flat claim (if you added a "roles-flat" mapper in Keycloak)
         e.g.,  "roles": ["data:read", "data:write"]
      2) The standard 'realm_access.roles' structure from Keycloak
         e.g.,  "realm_access": {"roles": ["data:read", "data:write"]}

    Returns:
        set[str]: unique, non-empty role names.
    """
    roles = set()

    # 1) Custom/flat roles claim (if you configured such a mapper in Keycloak)
    roles.update(claims.get("roles", []) or [])

    # 2) Standard Keycloak realm roles location
    roles.update((claims.get("realm_access") or {}).get("roles", []) or [])

    # OPTIONAL — include client roles as well (uncomment if you use them)
    # resource_access = claims.get("resource_access") or {}
    # for client_id, entry in resource_access.items():
    #     for r in entry.get("roles", []) or []:
    #         # Prefix client roles to avoid name collisions with realm roles
    #         roles.add(f"{client_id}:{r}")

    # Filter out any non-strings / empties, just in case
    return {r for r in roles if isinstance(r, str) and r}


class KeycloakOIDCBackend(OIDCAuthenticationBackend):
    """
    OIDC backend used by mozilla-django-oidc during login.

    Responsibilities:
      - Parse OIDC claims from Keycloak
      - Create a new Django user on first login (create_user)
      - Update the user on subsequent logins (update_user)
      - Sync Keycloak roles → Django Groups so your PolicyPermission can check them
    """

    # Called by the base class when no existing user matches the claims.
    def create_user(self, claims):
        """
        Create a new Django user on first OIDC login.

        Args:
            claims (dict): verified ID token claims (e.g., sub, email, names, roles...)

        Returns:
            User: the newly created Django user
        """
        # Preferred username is the most human-friendly identifier in Keycloak.
        # Fallback to 'sub' (the stable subject identifier) if needed.
        username = claims.get("preferred_username") or claims.get("sub")

        # Create a minimal user; no password is set (OIDC will handle auth).
        user = User.objects.create_user(
            username=username,
            email=claims.get("email", ""),
            first_name=(claims.get("given_name") or "")[:150],
            last_name=(claims.get("family_name") or "")[:150],
        )

        # Ensure Django groups mirror Keycloak roles immediately.
        self._sync_groups(user, claims)
        return user

    # Called by the base class when a matching user already exists.
    def update_user(self, user, claims):
        """
        Update existing Django user profile fields and resync groups.

        Args:
            user (User): existing Django user matched from claims
            claims (dict): verified ID token claims

        Returns:
            User: the updated user
        """
        # Keep user profile in sync with IdP data (safe truncation to field max length)
        user.email = claims.get("email", user.email)
        user.first_name = (claims.get("given_name") or user.first_name)[:150]
        user.last_name = (claims.get("family_name") or user.last_name)[:150]
        user.save(update_fields=["email", "first_name", "last_name"])

        # Keep roles (groups) in sync on every login
        self._sync_groups(user, claims)
        return user

    def _sync_groups(self, user, claims):
        """
        Make Django Groups *exactly* match the roles coming from Keycloak.

        Behavior:
          - For each incoming role, ensure a Django Group exists (get_or_create).
          - Replace the user's groups with that exact set (source-of-truth = Keycloak).
          - This means if a role is removed in Keycloak, it disappears in Django at next login.

        If you prefer "additive only" behavior (never remove), change user.groups.set(...)
        to a union/update pattern instead.
        """
        kc_roles = _extract_realm_roles(claims)

        groups = []
        for r in kc_roles:
            grp, _ = Group.objects.get_or_create(name=r)
            groups.append(grp)

        # Replace membership to exactly match Keycloak roles
        user.groups.set(groups)
        user.save()

        # OPTIONAL — map specific roles to Django staff/superuser:
        # if "admin" in kc_roles or "realm-admin" in kc_roles:
        #     user.is_staff = True
        #     # user.is_superuser = True  # if you truly want full Django superuser
        # else:
        #     user.is_staff = False
        # user.save(update_fields=["is_staff", "is_superuser"])

    def filter_users_by_claims(self, claims):
        """
        Return the queryset of users matching the incoming claims.

        The base class will use this to decide if create_user or update_user should run.

        We match on preferred_username (case-insensitive). If not present, fall back to 'sub'.
        """
        username = claims.get("preferred_username") or claims.get("sub")
        if not username:
            # No usable identifier → no match
            return self.UserModel.objects.none()
        return self.UserModel.objects.filter(username__iexact=username)
