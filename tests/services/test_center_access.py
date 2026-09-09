from __future__ import annotations

import importlib
from typing import Any, cast

import pytest
from django.apps import apps
from django.contrib.auth.models import AnonymousUser, User
from django.db.models.fields.related import ManyToManyField

from endoreg_db.models import Center, Examiner, PortalUserInfo
from endoreg_db.services.center_access import (
    CenterAccessConfigurationError,
    center_keys_from_group_paths,
    resolve_allowed_center_ids,
    synchronize_user_center_groups,
)

pytestmark = pytest.mark.django_db

CENTER_GROUP_CASES: tuple[tuple[list[str], frozenset[str]], ...] = (
    ([], frozenset()),
    (["/unrelated/a", " realm-role "], frozenset()),
    ([" /centers/north/ ", "/centers/south"], frozenset({"north", "south"})),
    (["/centers/north", "/centers/north"], frozenset({"north"})),
)


def _center(name: str, center_key: str) -> Center:
    return Center.objects.create(name=name, center_key=center_key)


def _portal_info(user: User, *centers: Center) -> PortalUserInfo:
    portal_info = PortalUserInfo.objects.create(user=user)
    portal_info.centers.set(centers)
    return portal_info


@pytest.mark.parametrize(
    ("group_paths", "expected"),
    CENTER_GROUP_CASES,
)
def test_center_keys_from_group_paths_only_accepts_center_groups(
    group_paths: list[str], expected: frozenset[str]
) -> None:
    assert center_keys_from_group_paths(group_paths) == expected


@pytest.mark.parametrize(
    "group_path",
    ("/centers/", "/centers///", "/centers/north/department"),
)
def test_center_keys_from_group_paths_rejects_malformed_center_groups(
    group_path: str,
) -> None:
    with pytest.raises(CenterAccessConfigurationError, match="Invalid Keycloak"):
        center_keys_from_group_paths([group_path])


def test_synchronize_user_center_groups_supports_zero_one_and_multiple_centers() -> (
    None
):
    north = _center("North", "north")
    south = _center("South", "south")
    user = User.objects.create_user(username="multi-center-user")

    assert synchronize_user_center_groups(user=user, group_paths=[]) == frozenset()
    portal_info = PortalUserInfo.objects.get(user=user)
    assert set(portal_info.centers.all()) == set()

    assert synchronize_user_center_groups(
        user=user, group_paths=["/centers/north"]
    ) == frozenset({north.pk})
    assert set(portal_info.centers.all()) == {north}

    assert synchronize_user_center_groups(
        user=user,
        group_paths=["/centers/south", "/roles/video-reader", "/centers/north"],
    ) == frozenset({north.pk, south.pk})
    assert set(portal_info.centers.all()) == {north, south}


def test_reauthentication_replaces_cached_center_memberships() -> None:
    north = _center("North", "north")
    south = _center("South", "south")
    user = User.objects.create_user(username="reauthenticated-user")
    portal_info = _portal_info(user, north)

    resolved = synchronize_user_center_groups(user=user, group_paths=["/centers/south"])

    assert resolved == frozenset({south.pk})
    assert set(portal_info.centers.all()) == {south}


@pytest.mark.parametrize(
    "group_paths",
    (["/centers/unknown"], ["/centers/south", "/centers/unknown"]),
)
def test_unknown_center_groups_fail_without_partial_assignment(
    group_paths: list[str],
) -> None:
    north = _center("North", "north")
    _center("South", "south")
    user = User.objects.create_user(username=f"unknown-center-{len(group_paths)}")
    portal_info = _portal_info(user, north)

    with pytest.raises(CenterAccessConfigurationError, match="unknown"):
        synchronize_user_center_groups(user=user, group_paths=group_paths)

    assert set(portal_info.centers.all()) == {north}


def test_malformed_center_group_does_not_replace_existing_membership() -> None:
    north = _center("North", "north")
    user = User.objects.create_user(username="malformed-center-user")
    portal_info = _portal_info(user, north)

    with pytest.raises(CenterAccessConfigurationError, match="Invalid Keycloak"):
        synchronize_user_center_groups(
            user=user, group_paths=["/centers/north/department"]
        )

    assert set(portal_info.centers.all()) == {north}


def test_resolve_allowed_center_ids_combines_typed_and_legacy_memberships() -> None:
    legacy = _center("Legacy", "legacy")
    assigned = _center("Assigned", "assigned")
    examiner = Examiner.objects.create(
        first_name="Legacy",
        last_name="Examiner",
        center=legacy,
        hash="legacy-center-access-examiner",
        is_real_person=False,
    )
    user = User.objects.create_user(username="legacy-center-user")
    portal_info = PortalUserInfo.objects.create(user=user, examiner=examiner)
    portal_info.centers.add(assigned)

    assert resolve_allowed_center_ids(user) == frozenset({legacy.pk, assigned.pk})


def test_resolve_allowed_center_ids_fails_closed_without_membership() -> None:
    user = User.objects.create_user(username="centerless-user")

    assert resolve_allowed_center_ids(AnonymousUser()) == frozenset()
    assert resolve_allowed_center_ids(user) == frozenset()
    _portal_info(user)
    assert resolve_allowed_center_ids(user) == frozenset()


@pytest.mark.parametrize("privilege", ("is_staff", "is_superuser"))
def test_resolve_allowed_center_ids_returns_explicit_global_access(
    privilege: str,
) -> None:
    user = User.objects.create_user(username=f"global-{privilege}")
    setattr(user, privilege, True)

    assert resolve_allowed_center_ids(user) is None


def test_portal_user_centers_is_a_typed_many_to_many_relationship() -> None:
    field = PortalUserInfo._meta.get_field("centers")

    assert isinstance(field, ManyToManyField)
    assert field.related_model is Center


def test_legacy_membership_data_migration_copies_examiner_center() -> None:
    center = _center("Migration center", "migration-center")
    examiner = Examiner.objects.create(
        first_name="Migration",
        last_name="Examiner",
        center=center,
        hash="migration-center-access-examiner",
        is_real_person=False,
    )
    user = User.objects.create_user(username="migration-center-user")
    portal_info = PortalUserInfo.objects.create(user=user, examiner=examiner)
    migration = importlib.import_module(
        "endoreg_db.migrations.0051_portaluserinfo_centers"
    )

    copy_examiner_centers = cast(Any, migration).copy_examiner_centers
    copy_examiner_centers(apps, None)

    assert set(portal_info.centers.all()) == {center}
