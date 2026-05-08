from __future__ import annotations

from django.conf import settings

VALID_DEPLOYMENT_ROLES = (
    "standalone",
    "site_node",
    "central_hub",
)


def get_deployment_role() -> str:
    role = str(getattr(settings, "ENDOREG_DEPLOYMENT_ROLE", "standalone") or "").strip()
    normalized = role.lower() or "standalone"
    if normalized not in VALID_DEPLOYMENT_ROLES:
        return "standalone"
    return normalized


def hub_mode_enabled() -> bool:
    return get_deployment_role() == "central_hub"


def transfer_api_enabled() -> bool:
    return get_deployment_role() == "central_hub"


def deployment_profile_payload() -> dict[str, object]:
    return {
        "deployment_role": get_deployment_role(),
        "hub_mode": hub_mode_enabled(),
        "transfer_api_enabled": transfer_api_enabled(),
        "transfer_require_secure_transport": bool(
            getattr(settings, "ENDOREG_HUB_TRANSFER_REQUIRE_SECURE_TRANSPORT", True)
        ),
        "transfer_require_mtls": bool(
            getattr(settings, "ENDOREG_HUB_TRANSFER_REQUIRE_MTLS", False)
        ),
        "transfer_mtls_meta_key": str(
            getattr(settings, "ENDOREG_HUB_TRANSFER_MTLS_META_KEY", "") or ""
        ).strip(),
        "transfer_mtls_meta_value": str(
            getattr(settings, "ENDOREG_HUB_TRANSFER_MTLS_META_VALUE", "") or ""
        ).strip(),
    }


__all__ = [
    "VALID_DEPLOYMENT_ROLES",
    "deployment_profile_payload",
    "get_deployment_role",
    "hub_mode_enabled",
    "transfer_api_enabled",
]
