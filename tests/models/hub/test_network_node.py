from __future__ import annotations

from typing import Protocol, cast

import pytest

from endoreg_db.models import NetworkNode


@pytest.mark.django_db
def test_network_node_auto_generates_unique_slug_key_from_display_name():
    first = NetworkNode.objects.create(display_name="Site A Node")
    second = NetworkNode.objects.create(display_name="Site A Node")

    assert first.node_key == "site-a-node"
    assert second.node_key == "site-a-node-2"


@pytest.mark.django_db
def test_network_node_auto_key_falls_back_to_node_for_blank_display_name():
    first = NetworkNode.objects.create(display_name="")
    second = NetworkNode.objects.create(display_name="")

    assert first.node_key == "node"
    assert second.node_key == "node-2"


@pytest.mark.django_db
def test_network_node_key_is_immutable_after_save():
    node = NetworkNode.objects.create(
        display_name="Immutable Node",
        node_key="immutable-node",
    )

    node.node_key = "changed-node"
    with pytest.raises(ValueError, match="immutable"):
        node.save()

    node.refresh_from_db()
    assert node.node_key == "immutable-node"


@pytest.mark.django_db
def test_network_node_build_node_key_excludes_current_record_when_renaming_display():
    node = NetworkNode.objects.create(
        display_name="Original Node",
        node_key="original-node",
    )

    candidate = NetworkNode.build_node_key("Original Node", exclude_pk=node.pk)

    assert candidate == "original-node"


@pytest.mark.django_db
def test_network_node_manager_get_by_node_key_returns_matching_node():
    node = NetworkNode.objects.create(
        display_name="Lookup Node",
        node_key="lookup-node",
    )

    assert cast(_NetworkNodeManagerLike, NetworkNode.objects).get_by_node_key("lookup-node") == node


@pytest.mark.django_db
def test_network_node_shared_secret_is_hashed_and_verified():
    node = NetworkNode.objects.create(
        display_name="Secret Node",
        node_key="secret-node",
    )

    node.set_shared_secret("  request-secret  ")
    node.save(update_fields=["shared_secret_hash"])
    node.refresh_from_db()

    assert node.shared_secret_hash
    assert node.shared_secret_hash != "request-secret"
    assert node.check_shared_secret("request-secret") is True
    assert node.check_shared_secret("  request-secret  ") is True
    assert node.check_shared_secret("wrong-secret") is False
    assert node.check_shared_secret("") is False


@pytest.mark.django_db
def test_network_node_rejects_empty_shared_secret():
    node = NetworkNode.objects.create(
        display_name="Secret Node",
        node_key="secret-node",
    )

    with pytest.raises(ValueError, match="must not be empty"):
        node.set_shared_secret("   ")

    assert node.shared_secret_hash == ""


class _NetworkNodeManagerLike(Protocol):
    def get_by_node_key(self, node_key: str):
        ...

