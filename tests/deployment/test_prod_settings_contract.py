from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.no_db


def _run_prod_settings_probe(
    env_overrides: dict[str, str],
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.pop("PYTEST_CURRENT_TEST", None)
    env.pop("DJANGO_REQUIRE_SECURE_PROXY_SSL_HEADER", None)
    env.pop("DJANGO_SECURE_PROXY_SSL_HEADER_NAME", None)
    env.pop("DJANGO_SECURE_PROXY_SSL_HEADER_VALUE", None)
    env.pop("WATCHER_CELERY_INLINE_FALLBACK_ENABLED", None)
    env.update(env_overrides)

    probe = """
import json
import os

os.environ["DJANGO_SETTINGS_MODULE"] = "endoreg_db.config.settings.prod"

from endoreg_db.config.settings import prod

secure_proxy_ssl_header = (
    list(prod.SECURE_PROXY_SSL_HEADER) if prod.SECURE_PROXY_SSL_HEADER else None
)

payload = {
    "debug": prod.DEBUG,
    "allowed_hosts": prod.ALLOWED_HOSTS,
    "endoreg_deployment_role": prod.ENDOREG_DEPLOYMENT_ROLE,
    "endoreg_enable_hub_transfers": prod.ENDOREG_ENABLE_HUB_TRANSFERS,
    "endoreg_hub_transfer_require_secure_transport": prod.ENDOREG_HUB_TRANSFER_REQUIRE_SECURE_TRANSPORT,
    "endoreg_hub_transfer_require_mtls": prod.ENDOREG_HUB_TRANSFER_REQUIRE_MTLS,
    "endoreg_hub_transfer_mtls_meta_key": prod.ENDOREG_HUB_TRANSFER_MTLS_META_KEY,
    "endoreg_hub_transfer_mtls_meta_value": prod.ENDOREG_HUB_TRANSFER_MTLS_META_VALUE,
    "secure_ssl_redirect": prod.SECURE_SSL_REDIRECT,
    "secure_proxy_ssl_header": secure_proxy_ssl_header,
    "session_cookie_secure": prod.SESSION_COOKIE_SECURE,
    "csrf_cookie_secure": prod.CSRF_COOKIE_SECURE,
    "oidc_verify_ssl": prod.OIDC_VERIFY_SSL,
    "watcher_celery_inline_fallback_enabled": prod.WATCHER_CELERY_INLINE_FALLBACK_ENABLED,
    "default_authentication_classes": list(prod.REST_FRAMEWORK["DEFAULT_AUTHENTICATION_CLASSES"]),
    "default_permission_classes": list(prod.REST_FRAMEWORK["DEFAULT_PERMISSION_CLASSES"]),
    "logging_formatter": prod.LOGGING["formatters"]["structured_json"]["()"],
    "logging_handler_class": prod.LOGGING["handlers"]["console"]["class"],
    "logging_handler_formatter": prod.LOGGING["handlers"]["console"]["formatter"],
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
    assert payload["endoreg_enable_hub_transfers"] is False
    assert payload["endoreg_hub_transfer_require_secure_transport"] is True
    assert payload["endoreg_hub_transfer_require_mtls"] is False
    assert payload["secure_ssl_redirect"] is True
    assert payload["secure_proxy_ssl_header"] is None
    assert payload["session_cookie_secure"] is True
    assert payload["csrf_cookie_secure"] is True
    assert payload["oidc_verify_ssl"] is True
    assert payload["watcher_celery_inline_fallback_enabled"] is False
    assert payload["default_authentication_classes"] == [
        "rest_framework.authentication.SessionAuthentication",
        "endoreg_db.authz.auth.KeycloakJWTAuthentication",
    ]
    assert payload["default_permission_classes"] == [
        "endoreg_db.utils.web.permissions.EnvironmentAwarePermission",
        "endoreg_db.authz.permissions.PolicyPermission",
    ]
    assert (
        payload["logging_formatter"]
        == "endoreg_db.utils.observability.structured_logging.StructuredJsonFormatter"
    )
    assert payload["logging_handler_class"] == "logging.StreamHandler"
    assert payload["logging_handler_formatter"] == "structured_json"


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
            "DJANGO_SECURE_PROXY_SSL_HEADER_NAME": "HTTP_X_FORWARDED_PROTO",
            "DJANGO_SECURE_PROXY_SSL_HEADER_VALUE": "https",
        }
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    assert payload["endoreg_deployment_role"] == "central_hub"
    assert payload["endoreg_enable_hub_transfers"] is False
    assert payload["secure_proxy_ssl_header"] == [
        "HTTP_X_FORWARDED_PROTO",
        "https",
    ]


def test_prod_settings_accept_enabled_central_hub_transfers() -> None:
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
            "ENDOREG_ENABLE_HUB_TRANSFERS": "true",
            "ENDOREG_HUB_TRANSFER_REQUIRE_MTLS": "true",
            "ENDOREG_HUB_TRANSFER_MTLS_META_KEY": "HTTP_X_CLIENT_CERT_VERIFIED",
            "ENDOREG_HUB_TRANSFER_MTLS_META_VALUE": "SUCCESS",
            "DJANGO_SECURE_PROXY_SSL_HEADER_NAME": "HTTP_X_FORWARDED_PROTO",
            "DJANGO_SECURE_PROXY_SSL_HEADER_VALUE": "https",
        }
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    assert payload["endoreg_deployment_role"] == "central_hub"
    assert payload["endoreg_enable_hub_transfers"] is True
    assert payload["secure_proxy_ssl_header"] == [
        "HTTP_X_FORWARDED_PROTO",
        "https",
    ]


def test_prod_settings_accept_proxy_https_header_contract() -> None:
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
            "DJANGO_REQUIRE_SECURE_PROXY_SSL_HEADER": "true",
            "DJANGO_SECURE_PROXY_SSL_HEADER_NAME": "HTTP_X_FORWARDED_PROTO",
            "DJANGO_SECURE_PROXY_SSL_HEADER_VALUE": "https",
        }
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    assert payload["secure_proxy_ssl_header"] == [
        "HTTP_X_FORWARDED_PROTO",
        "https",
    ]


def test_prod_settings_refuse_required_proxy_https_header_when_missing() -> None:
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
            "DJANGO_REQUIRE_SECURE_PROXY_SSL_HEADER": "true",
        }
    )

    assert result.returncode != 0
    assert (
        "DJANGO_REQUIRE_SECURE_PROXY_SSL_HEADER=true requires "
        "DJANGO_SECURE_PROXY_SSL_HEADER_NAME=HTTP_X_FORWARDED_PROTO" in result.stderr
    )


def test_prod_settings_refuse_unsafe_proxy_https_header_value() -> None:
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
            "DJANGO_SECURE_PROXY_SSL_HEADER_NAME": "HTTP_X_FORWARDED_PROTO",
            "DJANGO_SECURE_PROXY_SSL_HEADER_VALUE": "http",
        }
    )

    assert result.returncode != 0
    assert "DJANGO_SECURE_PROXY_SSL_HEADER_VALUE must be https" in result.stderr


def test_prod_settings_refuse_watcher_inline_fallback() -> None:
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
            "WATCHER_CELERY_INLINE_FALLBACK_ENABLED": "true",
        }
    )

    assert result.returncode != 0
    assert "WATCHER_CELERY_INLINE_FALLBACK_ENABLED must be false in production" in (
        result.stderr
    )


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
    assert payload["endoreg_enable_hub_transfers"] is False
    assert payload["endoreg_hub_transfer_require_secure_transport"] is True
    assert payload["endoreg_hub_transfer_require_mtls"] is False


def test_prod_settings_accept_local_study_server_role() -> None:
    protected_root = "/tmp/endoreg-local-study-server-contract/protected"
    result = _run_prod_settings_probe(
        {
            "DJANGO_DEBUG": "false",
            "DJANGO_SECRET_KEY": "x" * 64,
            "DJANGO_ALLOWED_HOSTS": "study.example.org",
            "DB_ENGINE": "django.db.backends.postgresql",
            "DB_NAME": "endoreg_study",
            "OIDC_RP_CLIENT_ID": "endoregdb-api",
            "OIDC_RP_CLIENT_SECRET": "test-secret",
            "ENDOREG_DEPLOYMENT_ROLE": "local_study_server",
            "LX_ANNOTATE_ENCRYPTED_DATA_DIR": protected_root,
            "STORAGE_DIR": f"{protected_root}/storage",
            "PROTECTED_MEDIA_ROOT": f"{protected_root}/storage",
        }
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    assert payload["endoreg_deployment_role"] == "local_study_server"
    assert payload["endoreg_enable_hub_transfers"] is False
    assert payload["endoreg_hub_transfer_require_mtls"] is False


def test_prod_settings_refuse_transfer_api_outside_central_hub_role() -> None:
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
            "ENDOREG_ENABLE_HUB_TRANSFERS": "true",
        }
    )

    assert result.returncode != 0
    assert (
        "ENDOREG_ENABLE_HUB_TRANSFERS=true requires ENDOREG_DEPLOYMENT_ROLE=central_hub"
        in result.stderr
    )


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
            "DJANGO_SECURE_PROXY_SSL_HEADER_NAME": "HTTP_X_FORWARDED_PROTO",
            "DJANGO_SECURE_PROXY_SSL_HEADER_VALUE": "https",
        }
    )

    assert result.returncode != 0
    assert (
        "ENDOREG_DEPLOYMENT_ROLE=central_hub requires ENDOREG_HUB_TRANSFER_REQUIRE_MTLS=true"
        in result.stderr
    )


def test_prod_settings_refuse_central_hub_without_proxy_https_header() -> None:
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

    assert result.returncode != 0
    assert (
        "ENDOREG_DEPLOYMENT_ROLE=central_hub requires "
        "DJANGO_SECURE_PROXY_SSL_HEADER_NAME=HTTP_X_FORWARDED_PROTO" in result.stderr
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


def test_prod_settings_refuse_sqlite_when_local_study_server_role_is_enabled() -> None:
    protected_root = "/tmp/endoreg-local-study-server-contract/protected"
    result = _run_prod_settings_probe(
        {
            "DJANGO_DEBUG": "false",
            "DJANGO_SECRET_KEY": "x" * 64,
            "DJANGO_ALLOWED_HOSTS": "study.example.org",
            "DB_ENGINE": "django.db.backends.sqlite3",
            "DB_NAME": str(
                REPO_ROOT / "data" / "tests" / "deployment_contract.sqlite3"
            ),
            "OIDC_RP_CLIENT_ID": "endoregdb-api",
            "OIDC_RP_CLIENT_SECRET": "test-secret",
            "ENDOREG_DEPLOYMENT_ROLE": "local_study_server",
            "LX_ANNOTATE_ENCRYPTED_DATA_DIR": protected_root,
            "STORAGE_DIR": f"{protected_root}/storage",
            "PROTECTED_MEDIA_ROOT": f"{protected_root}/storage",
        }
    )

    assert result.returncode != 0
    assert (
        "ENDOREG_DEPLOYMENT_ROLE=local_study_server requires a non-SQLite production database"
        in result.stderr
    )
