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
    "endoreg_deployment_role": prod.ENDOREG_DEPLOYMENT_ROLE,
    "endoreg_hub_transfer_require_secure_transport": prod.ENDOREG_HUB_TRANSFER_REQUIRE_SECURE_TRANSPORT,
    "endoreg_hub_transfer_require_mtls": prod.ENDOREG_HUB_TRANSFER_REQUIRE_MTLS,
    "endoreg_hub_transfer_mtls_meta_key": prod.ENDOREG_HUB_TRANSFER_MTLS_META_KEY,
    "endoreg_hub_transfer_mtls_meta_value": prod.ENDOREG_HUB_TRANSFER_MTLS_META_VALUE,
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
    assert payload["endoreg_deployment_role"] == "standalone"
    assert payload["endoreg_hub_transfer_require_secure_transport"] is True
    assert payload["endoreg_hub_transfer_require_mtls"] is False
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


def test_prod_settings_accept_central_hub_role() -> None:
    result = _run_prod_settings_probe(
        {
            "DJANGO_DEBUG": "false",
            "DJANGO_SECRET_KEY": "x" * 64,
            "DJANGO_ALLOWED_HOSTS": "annotate.example.org,api.example.org",
            "DB_ENGINE": "django.db.backends.postgresql",
            "DB_NAME": str(
                REPO_ROOT / "data" / "tests" / "deployment_contract.sqlite3"
            ),
            "OIDC_RP_CLIENT_ID": "endoregdb-api",
            "OIDC_RP_CLIENT_SECRET": "test-secret",
            "ENDOREG_DEPLOYMENT_ROLE": "central_hub",
            "ENDOREG_HUB_TRANSFER_REQUIRE_MTLS": "true",
            "ENDOREG_HUB_TRANSFER_MTLS_META_KEY": "HTTP_X_CLIENT_CERT_VERIFIED",
            "ENDOREG_HUB_TRANSFER_MTLS_META_VALUE": "SUCCESS",
        }
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    assert payload["endoreg_deployment_role"] == "central_hub"


def test_prod_settings_accept_site_node_role() -> None:
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
            "ENDOREG_DEPLOYMENT_ROLE": "site_node",
        }
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    assert payload["endoreg_deployment_role"] == "site_node"
    assert payload["endoreg_hub_transfer_require_secure_transport"] is True
    assert payload["endoreg_hub_transfer_require_mtls"] is False


def test_prod_settings_refuse_central_hub_without_mtls_requirement() -> None:
    result = _run_prod_settings_probe(
        {
            "DJANGO_DEBUG": "false",
            "DJANGO_SECRET_KEY": "x" * 64,
            "DJANGO_ALLOWED_HOSTS": "annotate.example.org,api.example.org",
            "DB_ENGINE": "django.db.backends.postgresql",
            "DB_NAME": str(
                REPO_ROOT / "data" / "tests" / "deployment_contract.sqlite3"
            ),
            "OIDC_RP_CLIENT_ID": "endoregdb-api",
            "OIDC_RP_CLIENT_SECRET": "test-secret",
            "ENDOREG_DEPLOYMENT_ROLE": "central_hub",
            "ENDOREG_HUB_TRANSFER_REQUIRE_MTLS": "false",
        }
    )

    assert result.returncode != 0
    assert (
        "ENDOREG_DEPLOYMENT_ROLE=central_hub requires ENDOREG_HUB_TRANSFER_REQUIRE_MTLS=true"
        in result.stderr
    )


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


def test_prod_settings_refuse_sqlite_when_central_hub_role_is_enabled() -> None:
    result = _run_prod_settings_probe(
        {
            "DJANGO_DEBUG": "false",
            "DJANGO_SECRET_KEY": "x" * 64,
            "DJANGO_ALLOWED_HOSTS": "annotate.example.org",
            "DB_ENGINE": "django.db.backends.sqlite3",
            "DB_NAME": str(
                REPO_ROOT / "data" / "tests" / "deployment_contract.sqlite3"
            ),
            "OIDC_RP_CLIENT_ID": "endoregdb-api",
            "OIDC_RP_CLIENT_SECRET": "test-secret",
            "ENDOREG_DEPLOYMENT_ROLE": "central_hub",
        }
    )

    assert result.returncode != 0
    assert (
        "ENDOREG_DEPLOYMENT_ROLE=central_hub requires a non-SQLite production database"
        in result.stderr
    )
