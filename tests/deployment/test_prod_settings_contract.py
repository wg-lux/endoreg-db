from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]


def _run_prod_settings_probe(
    env_overrides: dict[str, str],
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.pop("PYTEST_CURRENT_TEST", None)
    env.update(env_overrides)

    probe = """
import json
import os

os.environ["DJANGO_SETTINGS_MODULE"] = "endoreg_db.config.settings.prod"

from endoreg_db.config.settings import prod

payload = {
    "debug": prod.DEBUG,
    "allowed_hosts": prod.ALLOWED_HOSTS,
    "secure_ssl_redirect": prod.SECURE_SSL_REDIRECT,
    "session_cookie_secure": prod.SESSION_COOKIE_SECURE,
    "csrf_cookie_secure": prod.CSRF_COOKIE_SECURE,
    "oidc_verify_ssl": prod.OIDC_VERIFY_SSL,
    "default_authentication_classes": list(prod.REST_FRAMEWORK["DEFAULT_AUTHENTICATION_CLASSES"]),
    "default_permission_classes": list(prod.REST_FRAMEWORK["DEFAULT_PERMISSION_CLASSES"]),
}
print(json.dumps(payload))
"""

    return subprocess.run(
        [sys.executable, "-c", probe],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def test_prod_settings_accept_service_style_env_contract() -> None:
    result = _run_prod_settings_probe(
        {
            "DJANGO_DEBUG": "false",
            "DJANGO_SECRET_KEY": "x" * 64,
            "DJANGO_ALLOWED_HOSTS": "annotate.example.org,api.example.org",
            "DB_ENGINE": "django.db.backends.sqlite3",
            "DB_NAME": str(
                REPO_ROOT / "data" / "tests" / "deployment_contract.sqlite3"
            ),
            "OIDC_RP_CLIENT_ID": "endoregdb-api",
            "OIDC_RP_CLIENT_SECRET": "test-secret",
        }
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout.strip().splitlines()[-1])

    assert payload["debug"] is False
    assert payload["allowed_hosts"] == ["annotate.example.org", "api.example.org"]
    assert payload["secure_ssl_redirect"] is True
    assert payload["session_cookie_secure"] is True
    assert payload["csrf_cookie_secure"] is True
    assert payload["oidc_verify_ssl"] is True
    assert payload["default_authentication_classes"] == [
        "rest_framework.authentication.SessionAuthentication",
        "endoreg_db.authz.auth.KeycloakJWTAuthentication",
    ]
    assert payload["default_permission_classes"] == [
        "endoreg_db.utils.permissions.EnvironmentAwarePermission",
        "endoreg_db.authz.permissions.PolicyPermission",
    ]


def test_prod_settings_refuse_debug_mode_even_if_other_env_is_present() -> None:
    result = _run_prod_settings_probe(
        {
            "DJANGO_DEBUG": "true",
            "DJANGO_SECRET_KEY": "x" * 64,
            "DJANGO_ALLOWED_HOSTS": "annotate.example.org",
            "DB_ENGINE": "django.db.backends.sqlite3",
            "DB_NAME": str(
                REPO_ROOT / "data" / "tests" / "deployment_contract.sqlite3"
            ),
            "OIDC_RP_CLIENT_ID": "endoregdb-api",
            "OIDC_RP_CLIENT_SECRET": "test-secret",
        }
    )

    assert result.returncode != 0
    assert "DJANGO_DEBUG must be false in production" in result.stderr
