from __future__ import annotations

from django.conf import settings
from lx_dtypes.models.contracts.application_settings import (
    ApplicationSettingsDeploymentProfilePayload,
    ApplicationSettingsDeploymentRole,
)

VALID_DEPLOYMENT_ROLES = (
    "standalone",
    "site_node",
    "local_study_server",
    "central_hub",
)


def get_deployment_role() -> ApplicationSettingsDeploymentRole:
    role = str(getattr(settings, "ENDOREG_DEPLOYMENT_ROLE", "standalone") or "").strip()
    normalized = role.lower() or "standalone"
    if normalized not in VALID_DEPLOYMENT_ROLES:
        return "standalone"
    return normalized


def hub_mode_enabled() -> bool:
    return get_deployment_role() == "central_hub"


def local_study_server_mode_enabled() -> bool:
    return get_deployment_role() == "local_study_server"


def _coerce_bool_setting(setting_name: str) -> bool:
    value = getattr(settings, setting_name, False)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _incoming_hub_transfers_enabled() -> bool:
    return _coerce_bool_setting("ENDOREG_ENABLE_HUB_TRANSFERS") or _coerce_bool_setting(
        "ENDOREG_ENABLE_INCOMING_HUB_TRANSFERS",
    )


def transfer_api_enabled() -> bool:
    return get_deployment_role() == "central_hub" and _incoming_hub_transfers_enabled()


def deployment_profile_payload() -> ApplicationSettingsDeploymentProfilePayload:
    return ApplicationSettingsDeploymentProfilePayload(
        deployment_role=get_deployment_role(),
        hub_mode=hub_mode_enabled(),
        enable_hub_transfers=_incoming_hub_transfers_enabled(),
        transfer_api_enabled=transfer_api_enabled(),
        transfer_require_secure_transport=bool(
            getattr(settings, "ENDOREG_HUB_TRANSFER_REQUIRE_SECURE_TRANSPORT", True)
        ),
        transfer_require_mtls=bool(
            getattr(settings, "ENDOREG_HUB_TRANSFER_REQUIRE_MTLS", False)
        ),
    )


__all__ = [
    "VALID_DEPLOYMENT_ROLES",
    "deployment_profile_payload",
    "get_deployment_role",
    "hub_mode_enabled",
    "local_study_server_mode_enabled",
    "transfer_api_enabled",
]
