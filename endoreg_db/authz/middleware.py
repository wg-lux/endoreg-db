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

# Any URL path that starts with one of these prefixes is considered "protected" for browser UX.
# You can add more prefixes if you want the same login-redirect behavior elsewhere
# (e.g., PROTECTED_PREFIXES = ("/api/", "/reports/", "/dashboard/")).
# PROTECTED_PREFIXES = ("/api/",)

# Protect the SPA shell too (everything except static/assets/oidc)
PROTECTED_PREFIXES = ("/",)  # catch-all; we'll skip known public paths below

PUBLIC_PREFIXES = (
    "/static/",
    "/assets/",
    "/media/",
    "/favicon.ico",
    "/oidc/",  # OIDC endpoints must stay public
    "/__vite",  # if Vite dev assets ever used
)


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
        self.get_response = get_response

    def __call__(self, request):
        # request.path is the URL path without scheme/host/query (e.g., "/api/patients/").
        # If for any reason it's None/empty, coerce to empty string so startswith won’t explode.
        path = request.path or ""
        # --- Exclusions so we don't block assets, HMR, OIDC endpoints, favicon, etc.
        # Allow static, assets, vite HMR, favicon, and OIDC endpoints without redirect
        # Skip public stuff
        if path.startswith(PUBLIC_PREFIXES):
            return self.get_response(request)

        # If not protected, pass through (shouldn’t happen with PROTECTED_PREFIXES=('/' ,))
        if not path.startswith(PROTECTED_PREFIXES):
            return self.get_response(request)

        # API/token clients never get redirected
        auth = request.META.get("HTTP_AUTHORIZATION", "")
        if auth.startswith("Bearer "):
            return self.get_response(request)

        # 3) Browser without session → redirect to OIDC
        if not request.user.is_authenticated:
            from django.conf import settings
            from urllib.parse import urlencode

            params = urlencode({"next": request.get_full_path()})
            return redirect(f"{settings.LOGIN_URL}?{params}")

        # 4) Authenticated → pass through
        return self.get_response(request)
