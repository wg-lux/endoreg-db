from __future__ import annotations

import pytest

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
