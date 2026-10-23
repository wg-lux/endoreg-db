from __future__ import annotations

from django.test import override_settings

from endoreg_db.services.hub import deployment


def test_get_deployment_role_normalizes_case_and_falls_back_invalid() -> None:
    with override_settings(ENDOREG_DEPLOYMENT_ROLE="Central_Hub"):
        assert deployment.get_deployment_role() == "central_hub"

    with override_settings(ENDOREG_DEPLOYMENT_ROLE="not_a_role"):
        assert deployment.get_deployment_role() == "standalone"


def test_hub_and_study_server_mode_flags() -> None:
    with override_settings(ENDOREG_DEPLOYMENT_ROLE="central_hub"):
        assert deployment.hub_mode_enabled() is True
        assert deployment.local_study_server_mode_enabled() is False

    with override_settings(ENDOREG_DEPLOYMENT_ROLE="local_study_server"):
        assert deployment.hub_mode_enabled() is False
        assert deployment.local_study_server_mode_enabled() is True


def test_transfer_api_enabled_requires_central_hub_and_flag() -> None:
    with override_settings(
        ENDOREG_DEPLOYMENT_ROLE="central_hub",
        ENDOREG_ENABLE_HUB_TRANSFERS=True,
    ):
        assert deployment.transfer_api_enabled() is True

    with override_settings(
        ENDOREG_DEPLOYMENT_ROLE="site_node",
        ENDOREG_ENABLE_HUB_TRANSFERS=True,
    ):
        assert deployment.transfer_api_enabled() is False

    with override_settings(
        ENDOREG_DEPLOYMENT_ROLE="central_hub",
        ENDOREG_ENABLE_HUB_TRANSFERS=False,
    ):
        assert deployment.transfer_api_enabled() is False


def test_deployment_profile_payload_excludes_internal_mtls_metadata() -> None:
    with override_settings(
        ENDOREG_DEPLOYMENT_ROLE="central_hub",
        ENDOREG_ENABLE_HUB_TRANSFERS=True,
        ENDOREG_HUB_TRANSFER_REQUIRE_SECURE_TRANSPORT=False,
        ENDOREG_HUB_TRANSFER_REQUIRE_MTLS=True,
        ENDOREG_HUB_TRANSFER_MTLS_META_KEY="  node-key  ",
        ENDOREG_HUB_TRANSFER_MTLS_META_VALUE="  secret  ",
    ):
        payload = deployment.deployment_profile_payload()

    assert payload.deployment_role == "central_hub"
    assert payload.hub_mode is True
    assert payload.enable_hub_transfers is True
    assert payload.transfer_api_enabled is True
    assert payload.transfer_require_secure_transport is False
    assert payload.transfer_require_mtls is True
    assert "transfer_mtls_meta_key" not in payload.model_fields_set
    assert "transfer_mtls_meta_value" not in payload.model_fields_set
