from __future__ import annotations

from typing import cast

from endoreg_db.integrations.lx_dtypes_host_models import (
    ReportTemplateCapability,
    report_template_access_allowed,
)


class _Groups:
    def __init__(self, roles: object) -> None:
        self.roles = roles

    def values_list(self, _field: str, *, flat: bool) -> object:
        assert flat
        return self.roles


class _Actor:
    is_superuser = False
    is_staff = False
    groups: object

    def __init__(self, roles: object) -> None:
        self.groups = _Groups(roles)


def test_report_template_write_satisfies_read() -> None:
    actor = _Actor(("report_template:write",))

    assert report_template_access_allowed(actor, "report_template:write")
    assert report_template_access_allowed(actor, "report_template:read")


def test_report_template_read_does_not_satisfy_write() -> None:
    actor = _Actor(("report_template:read",))

    assert report_template_access_allowed(actor, "report_template:read")
    assert not report_template_access_allowed(actor, "report_template:write")


def test_global_data_write_policy_is_preserved() -> None:
    actor = _Actor(("data:write",))

    assert report_template_access_allowed(actor, "report_template:read")
    assert report_template_access_allowed(actor, "report_template:write")


def test_staff_and_superuser_follow_existing_policy_pattern() -> None:
    staff = _Actor(None)
    staff.is_staff = True
    superuser = _Actor(None)
    superuser.is_superuser = True

    assert report_template_access_allowed(staff, "report_template:write")
    assert report_template_access_allowed(superuser, "report_template:read")


def test_missing_or_invalid_group_relationship_fails_closed() -> None:
    missing_groups = object()
    invalid_roles = _Actor(("report_template:read", 7))
    broken_relation = _Actor(None)
    broken_relation.groups = object()

    assert not report_template_access_allowed(missing_groups, "report_template:read")
    assert not report_template_access_allowed(invalid_roles, "report_template:read")
    assert not report_template_access_allowed(broken_relation, "report_template:read")


def test_invalid_runtime_capability_fails_closed() -> None:
    actor = _Actor(("report_template:write",))

    assert not report_template_access_allowed(
        actor,
        cast(ReportTemplateCapability, "report_template:publish"),
    )
