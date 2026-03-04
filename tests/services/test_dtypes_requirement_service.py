from __future__ import annotations

from django.test import TestCase, override_settings

from endoreg_db.services.dtypes_requirement_service import (
    DEFAULT_LOOKUP_DTYPES_MODULE,
    DEFAULT_LOOKUP_REQUIREMENT_LEGACY_FALLBACK_ENABLED,
    DEFAULT_LOOKUP_REQUIREMENT_SOURCE,
    get_lookup_dtypes_module_name,
    get_lookup_requirement_legacy_fallback_enabled,
    get_lookup_requirement_source,
)


class DtypesRequirementServiceConfigTests(TestCase):
    @override_settings(LOOKUP_REQUIREMENT_SOURCE="legacy_db")
    def test_get_lookup_requirement_source_accepts_legacy_db(self):
        assert get_lookup_requirement_source() == "legacy_db"

    @override_settings(LOOKUP_REQUIREMENT_SOURCE="dtypes")
    def test_get_lookup_requirement_source_accepts_dtypes(self):
        assert get_lookup_requirement_source() == "dtypes"

    @override_settings(LOOKUP_REQUIREMENT_SOURCE="hybrid_compare")
    def test_get_lookup_requirement_source_accepts_hybrid_compare(self):
        assert get_lookup_requirement_source() == "hybrid_compare"

    @override_settings(LOOKUP_REQUIREMENT_SOURCE="not_a_mode")
    def test_get_lookup_requirement_source_falls_back_for_unknown_value(self):
        assert get_lookup_requirement_source() == DEFAULT_LOOKUP_REQUIREMENT_SOURCE

    @override_settings(LOOKUP_REQUIREMENT_LEGACY_FALLBACK_ENABLED=True)
    def test_get_lookup_requirement_legacy_fallback_enabled_true(self):
        assert get_lookup_requirement_legacy_fallback_enabled() is True

    @override_settings(LOOKUP_REQUIREMENT_LEGACY_FALLBACK_ENABLED=False)
    def test_get_lookup_requirement_legacy_fallback_enabled_false(self):
        assert get_lookup_requirement_legacy_fallback_enabled() is False

    @override_settings()
    def test_get_lookup_requirement_legacy_fallback_enabled_default(self):
        assert (
            get_lookup_requirement_legacy_fallback_enabled()
            == DEFAULT_LOOKUP_REQUIREMENT_LEGACY_FALLBACK_ENABLED
        )

    @override_settings(LOOKUP_DTYPES_MODULE_NAME="report_template_examples")
    def test_get_lookup_dtypes_module_name_uses_setting(self):
        assert get_lookup_dtypes_module_name() == "report_template_examples"

    @override_settings(LOOKUP_DTYPES_MODULE_NAME="")
    def test_get_lookup_dtypes_module_name_falls_back_for_empty_string(self):
        assert get_lookup_dtypes_module_name() == DEFAULT_LOOKUP_DTYPES_MODULE
