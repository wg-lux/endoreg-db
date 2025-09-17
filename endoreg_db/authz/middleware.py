# endoreg_db/authz/middleware.py
#
# Purpose:
#   - For *browser requests* that hit protected API URLs (e.g., /api/...), make sure the user
#     is authenticated via Keycloak. If not, redirect them to the OIDC login view and remember
#     the original URL in ?next= so they come back to the same endpoint after login.
#   - For *API clients* sending a Bearer token, DO NOT redirect (that would break API usage).
#     Let DRF handle authentication/authorization and return 401/403 as appropriate.
#
# How it integrates:
#   - This middleware is appended in settings via KEYCLOAK.EXTRA_MIDDLEWARE (in dev.py).
#   - It assumes AuthenticationMiddleware has already run (declared in base.py), so
#     request.user is available and accurate.
#
# Security model:
#   - We only redirect *browser* requests (no Authorization header) that target protected prefixes.
#   - We attach the original URL as ?next=<relative-path>. mozilla-django-oidc will read this
#     and redirect back after a successful login.
#   - Optional: you can sanitize/validate the next parameter to avoid open redirects,
#     though using a relative path from request.get_full_path() is already safe.

from django.shortcuts import redirect
from django.conf import settings
from urllib.parse import urlencode

# Any URL path that starts with one of these prefixes is considered "protected" for browser UX.
# You can add more prefixes if you want the same login-redirect behavior elsewhere
# (e.g., PROTECTED_PREFIXES = ("/api/", "/reports/", "/dashboard/")).
PROTECTED_PREFIXES = ("/api/",)


class LoginRequiredForAPIsMiddleware:
    """
    For browser traffic:
      - If a user hits a protected URL without being authenticated, redirect to OIDC login
        and include ?next=<original-url> so the user returns to the same endpoint post-login.
    For API clients:
      - If the request has an "Authorization: Bearer <token>" header, do not redirect;
        let DRF auth handle it (token flows expect 401/403, not 302).
    """

    def __init__(self, get_response):
        # Standard Django middleware signature. We store the callable that processes the request
        # once our check is done (or skipped).
        self.get_response = get_response

    def __call__(self, request):
        # request.path is the URL path without scheme/host/query (e.g., "/api/patients/").
        # If for any reason it's None/empty, coerce to empty string so startswith won’t explode.
        path = request.path or ""

        # 1) If the path isn't in a "protected" area, we do nothing and pass through.
        #    This keeps non-API pages (like the public home) unaffected by this middleware.
        if not path.startswith(PROTECTED_PREFIXES):
            return self.get_response(request)

        # 2) Detect token-based API calls. API clients should *not* be redirected to a login page.
        #    They expect proper HTTP codes from DRF (401/403) based on Bearer token validity.
        #    NOTE: HTTP headers come in via request.META with "HTTP_" prefix by convention.
        auth = request.META.get("HTTP_AUTHORIZATION", "")
        if auth.startswith("Bearer "):
            # Let the request continue to DRF; authentication classes will validate the JWT.
            return self.get_response(request)

        # 3) Browser path under /api/ without a Django session → redirect to OIDC login.
        #    request.user is set by AuthenticationMiddleware. If not authenticated, we bounce
        #    to settings.LOGIN_URL (mozilla-django-oidc's /oidc/authenticate/) with ?next=<full path>.
        if not request.user.is_authenticated:
            # request.get_full_path() returns the path *including* the query string,
            # e.g., "/api/patients/?page=2&search=abc". This preserves pagination/filters after login.
            next_value = request.get_full_path()

            # --- Optional hardening (uncomment to enforce relative "next"):
            # from django.utils.http import url_has_allowed_host_and_scheme
            # if not url_has_allowed_host_and_scheme(
            #     url=next_value,
            #     allowed_hosts={request.get_host()},  # only allow current host
            #     require_https=request.is_secure(),
            # ):
            #     next_value = "/"  # fallback to a safe default

            # Build the query string (?next=...) and redirect to the OIDC login view.
            params = urlencode({"next": next_value})
            return redirect(f"{settings.LOGIN_URL}?{params}")

        # 4) Already authenticated in the browser? Great—just continue to the view.
        return self.get_response(request)
