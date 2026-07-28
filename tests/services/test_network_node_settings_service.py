from __future__ import annotations

import pytest

from endoreg_db.models import Center
from endoreg_db.models import NetworkNode
from endoreg_db.services.hub.network_nodes import (
    NetworkNodeValidationError,
    create_network_node,
    update_network_node,
)


@pytest.mark.django_db
def test_create_network_node_rejects_invalid_role():
    with pytest.raises(NetworkNodeValidationError) as exc_info:
        create_network_node(
            {
                "display_name": "Invalid Role Node",
                "role": "not-a-role",
            }
        )

    assert exc_info.value.errors == {"role": "Invalid role."}
    assert not NetworkNode.objects.filter(display_name="Invalid Role Node").exists()


@pytest.mark.django_db
def test_update_network_node_rejects_node_key_change():
    node = NetworkNode.objects.create(
        display_name="Immutable Node",
        node_key="immutable-node",
    )

    with pytest.raises(NetworkNodeValidationError) as exc_info:
        update_network_node(node, {"node_key": "changed-node"})

    assert exc_info.value.errors == {
        "node_key": "node_key is immutable once assigned.",
    }
    node.refresh_from_db()
    assert node.node_key == "immutable-node"


@pytest.mark.django_db
def test_update_network_node_updates_shared_secret_through_model_method():
    node = NetworkNode.objects.create(
        display_name="Secret Node",
        node_key="secret-node",
    )
    node.set_shared_secret("old-secret")
    node.save(update_fields=["shared_secret_hash"])

    updated = update_network_node(node, {"shared_secret": "new-secret"})

    assert updated.check_shared_secret("new-secret") is True
    assert updated.check_shared_secret("old-secret") is False
    assert updated.shared_secret_hash
    assert updated.shared_secret_hash != "new-secret"


@pytest.mark.django_db
def test_update_network_node_clear_shared_secret_removes_hash():
    node = NetworkNode.objects.create(
        display_name="Clear Secret Node",
        node_key="clear-secret-node",
    )
    node.set_shared_secret("request-secret")
    node.save(update_fields=["shared_secret_hash"])

    updated = update_network_node(node, {"clear_shared_secret": True})

    assert updated.shared_secret_hash == ""
    assert updated.check_shared_secret("request-secret") is False


@pytest.mark.django_db
def test_create_network_node_rejects_non_string_shared_secret():
    with pytest.raises(NetworkNodeValidationError) as exc_info:
        create_network_node(
            {
                "display_name": "Typed Secret Node",
                "shared_secret": 1234,
            }
        )

    assert exc_info.value.errors == {"shared_secret": "shared_secret must be a string."}
    assert not NetworkNode.objects.filter(display_name="Typed Secret Node").exists()


@pytest.mark.django_db
def test_create_network_node_aggregates_errors_without_writing():
    existing = NetworkNode.objects.create(
        display_name="Existing Node",
        node_key="existing-node-key",
    )
    initial_count = NetworkNode.objects.count()

    with pytest.raises(NetworkNodeValidationError) as exc_info:
        create_network_node(
            {
                "display_name": " ",
                "role": "invalid-role",
                "node_key": existing.node_key,
                "is_active": "yes",
                "owning_center_id": 999_999,
                "shared_secret": 123,
            }
        )

    assert exc_info.value.errors == {
        "display_name": "display_name is required.",
        "role": "Invalid role.",
        "is_active": "is_active must be a boolean.",
        "owning_center": "Owning center not found.",
        "shared_secret": "shared_secret must be a string.",
        "node_key": "node_key already exists.",
    }
    assert NetworkNode.objects.count() == initial_count


@pytest.mark.django_db
def test_update_network_node_rejects_invalid_clear_shared_secret_type():
    node = NetworkNode.objects.create(
        display_name="Invalid Clear Flag Node",
        node_key="invalid-clear-flag",
    )

    with pytest.raises(NetworkNodeValidationError) as exc_info:
        update_network_node(node, {"clear_shared_secret": "yes"})

    assert exc_info.value.errors == {
        "clear_shared_secret": "clear_shared_secret must be a boolean."
    }
    node.refresh_from_db()
    assert node.shared_secret_hash == ""


@pytest.mark.django_db
def test_create_network_node_generates_missing_node_key_and_defaults():
    center = Center.objects.create(
        name="default-center-node", display_name="Default Center"
    )
    created = create_network_node(
        {
            "display_name": "Auto Node",
            "owning_center_key": center.center_key,
        }
    )

    assert created.role == NetworkNode.Role.SITE_NODE
    assert created.base_url == ""
    assert created.node_key != ""
    assert created.owning_center == center


@pytest.mark.django_db
def test_update_network_node_updates_owning_center_by_id_and_allows_clear():
    center_one = Center.objects.create(name="center-one", display_name="Center One")
    center_two = Center.objects.create(name="center-two", display_name="Center Two")
    node = NetworkNode.objects.create(
        display_name="Center Change Node",
        node_key="center-change-node",
        owning_center=center_one,
    )

    updated = update_network_node(
        node,
        {
            "owning_center_id": center_two.pk,
            "display_name": "Center Change Node",
        },
    )
    assert updated.owning_center == center_two

    updated = update_network_node(
        node,
        {
            "owning_center_id": 0,
            "base_url": "https://example.org",
        },
    )
    assert updated.owning_center is None
    assert updated.base_url == "https://example.org"


@pytest.mark.django_db
def test_update_network_node_updates_display_name_and_role():
    node = NetworkNode.objects.create(
        display_name="Initial Name",
        node_key="identity-update",
    )

    updated = update_network_node(
        node,
        {
            "display_name": "  Renamed Node  ",
            "role": NetworkNode.Role.STANDALONE.value,
            "base_url": "  https://node.internal/  ",
        },
    )

    assert updated.display_name == "Renamed Node"
    assert updated.role == NetworkNode.Role.STANDALONE.value
    assert updated.base_url == "https://node.internal/"


@pytest.mark.django_db
def test_update_network_node_aggregates_errors_before_mutating_fields():
    node = NetworkNode.objects.create(
        display_name="Unchanged Node",
        node_key="unchanged-node",
        base_url="https://original.example/",
    )

    with pytest.raises(NetworkNodeValidationError) as exc_info:
        update_network_node(
            node,
            {
                "node_key": "changed-node",
                "display_name": " ",
                "role": "invalid-role",
                "base_url": "https://should-not-apply.example/",
                "is_active": "yes",
                "owning_center_id": 999_999,
                "shared_secret": 123,
                "clear_shared_secret": None,
            },
        )

    assert exc_info.value.errors == {
        "node_key": "node_key is immutable once assigned.",
        "display_name": "display_name must not be blank.",
        "role": "Invalid role.",
        "is_active": "is_active must be a boolean.",
        "owning_center": "Owning center not found.",
        "shared_secret": "shared_secret must be a string.",
        "clear_shared_secret": "clear_shared_secret must be a boolean.",
    }
    node.refresh_from_db()
    assert node.display_name == "Unchanged Node"
    assert node.base_url == "https://original.example/"
    assert node.is_active is True


@pytest.mark.django_db
def test_update_network_node_clear_wins_over_shared_secret_update():
    node = NetworkNode.objects.create(
        display_name="Rotate And Clear Node",
        node_key="rotate-and-clear-node",
    )

    updated = update_network_node(
        node,
        {
            "shared_secret": "transient-secret",
            "clear_shared_secret": True,
        },
    )

    assert updated.shared_secret_hash == ""
    assert updated.check_shared_secret("transient-secret") is False
