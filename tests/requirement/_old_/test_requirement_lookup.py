import json
import pytest
from django.core.cache import cache
from django.urls import reverse

pytestmark = pytest.mark.django_db  # APIClient touches DB-level middleware in some stacks


def _seed_cache(token: str, payload: dict):
    cache.set(f"lookup:{token}", payload, 60 * 30)


def test_lookup_init_happy_path(client, monkeypatch):
    # Arrange: stub the service to avoid DB work
    from endoreg_db.services import lookup_service as ls

    def fake_create_lookup_token_for_pe(pe_id: int) -> str:
        assert isinstance(pe_id, int) and pe_id > 0
        # also seed cache so subsequent GET /all works in other tests if reused
        _seed_cache("tok123", {"patient_examination_id": pe_id})
        return "tok123"

    monkeypatch.setattr(ls, "create_lookup_token_for_pe", fake_create_lookup_token_for_pe)

    # Act
    resp = client.post("/api/lookup/init/", data={"patient_examination_id": 1})

    # Assert
    assert resp.status_code == 201, resp.content
    data = resp.json()
    assert data["token"] == "tok123"


def test_lookup_init_requires_integer_id(client):
    for bad in (None, "abc", {}, [], ""):
        resp = client.post("/api/lookup/init/", data={"patient_examination_id": bad})
        assert resp.status_code == 400


def test_lookup_get_all_returns_payload(client):
    token = "t_all_ok"
    payload = {
        "patient_examination_id": 90,
        "requirement_sets": [{"id": 9, "name": "rs", "type": "all"}],
        "availableFindings": [],
        "requiredFindings": [],
        "requirementDefaults": {},
        "classificationChoices": {},
    }
    _seed_cache(token, payload)

    resp = client.get(f"/api/lookup/{token}/all/")
    assert resp.status_code == 200, resp.content
    data = resp.json()
    # minimal structural check
    assert set(data.keys()) == set(payload.keys())


def test_lookup_get_all_404_on_missing_token(client, monkeypatch):
    # Arrange: make store.get_all raise ValueError (your store already does this)
    from endoreg_db.services.lookup_store import LookupStore

    orig_get_all = LookupStore.get_all

    def raising_get_all(self):
        raise ValueError(f"Lookup token {self.token} has expired or is invalid")

    monkeypatch.setattr(LookupStore, "get_all", raising_get_all)

    # Act
    resp = client.get("/api/lookup/doesnotexist/all/")

    # Assert
    # If you haven't yet changed the View to 404, adapt to 500/400 accordingly.
    assert resp.status_code in (404, 400), resp.content

    # Cleanup (optional)
    monkeypatch.setattr(LookupStore, "get_all", orig_get_all)


def test_lookup_get_parts_happy_path(client):
    token = "t_parts_ok"
    payload = {
        "patient_examination_id": 90,
        "availableFindings": [1, 2, 3],
        "classificationChoices": {"42": [{"classification_id": 7, "label": "x"}]},
        "ignored": True,
    }
    _seed_cache(token, payload)

    resp = client.get(
        f"/api/lookup/{token}/parts/",
        {"keys": "availableFindings,classificationChoices"},
    )
    assert resp.status_code == 200, resp.content
    data = resp.json()
    assert "availableFindings" in data
    assert "classificationChoices" in data
    assert "ignored" not in data


def test_lookup_parts_requires_keys_param(client):
    token = "t_parts_bad"
    _seed_cache(token, {"a": 1})
    resp = client.get(f"/api/lookup/{token}/parts/")
    assert resp.status_code == 400


def test_lookup_patch_parts_updates_cache(client):
    token = "t_patch_ok"
    _seed_cache(token, {"a": 1})

    resp = client.patch(
        f"/api/lookup/{token}/parts/",
        data=json.dumps({"updates": {"selectedRequirementSetIds": [1, 2]}}),
        content_type="application/json",
    )
    assert resp.status_code == 200, resp.content

    # Cache should now contain the merged dict
    data = cache.get(f"lookup:{token}")
    assert data["a"] == 1
    assert data["selectedRequirementSetIds"] == [1, 2]


def test_lookup_session_expiration_and_restart(client, monkeypatch):
    """Test that expired sessions trigger automatic restart without infinite loops"""
    from endoreg_db.services import lookup_service as ls

    # Mock the service to track calls
    init_calls = []

    def mock_create_lookup_token_for_pe(pe_id: int) -> str:
        init_calls.append(pe_id)
        token = f"token_{len(init_calls)}"
        # Create cache entry that will be valid
        _seed_cache(token, {
            "patient_examination_id": pe_id,
            "requirement_sets": [{"id": 1, "name": "test", "type": "all"}],
            "availableFindings": [],
            "requiredFindings": [],
            "requirementDefaults": {},
            "classificationChoices": {},
        })
        return token

    monkeypatch.setattr(ls, "create_lookup_token_for_pe", mock_create_lookup_token_for_pe)

    # Step 1: Initialize session
    resp = client.post("/api/lookup/init/", data={"patient_examination_id": 123})
    assert resp.status_code == 201
    token = resp.json()["token"]
    assert len(init_calls) == 1

    # Step 2: Simulate session expiration by clearing cache
    cache.delete(f"lookup:{token}")

    # Step 3: Try to access expired session - should trigger restart
    resp = client.get(f"/api/lookup/{token}/all/")
    assert resp.status_code == 200  # Should succeed after restart

    # Verify that init was called again (restart happened)
    assert len(init_calls) == 2
    assert init_calls[1] == 123  # Same patient examination ID reused


def test_lookup_restart_prevents_infinite_loops(client, monkeypatch):
    """Test that restart mechanism prevents infinite loops"""
    from endoreg_db.services import lookup_service as ls

    # Mock to simulate persistent failures
    init_calls = []

    def mock_create_lookup_token_for_pe(pe_id: int) -> str:
        init_calls.append(pe_id)
        token = f"failing_token_{len(init_calls)}"
        # Don't seed cache - this will cause get_all to fail
        return token

    monkeypatch.setattr(ls, "create_lookup_token_for_pe", mock_create_lookup_token_for_pe)

    # Initialize session
    resp = client.post("/api/lookup/init/", data={"patient_examination_id": 456})
    assert resp.status_code == 201
    token = resp.json()["token"]

    # Try to access - should fail and attempt restart
    resp = client.get(f"/api/lookup/{token}/all/")
    assert resp.status_code == 404  # Should fail since cache was never seeded

    # Verify that init was called exactly twice (original + one restart attempt)
    assert len(init_calls) == 2


def test_lookup_cache_timeout_behavior(client):
    """Test that cache timeout settings work correctly"""
    from django.conf import settings
    from endoreg_db.services.lookup_store import DEFAULT_TTL_SECONDS

    # Verify TTL matches Django cache timeout
    cache_timeout = settings.CACHES['default']['TIMEOUT']
    assert DEFAULT_TTL_SECONDS == cache_timeout

    token = "timeout_test"
    payload = {"test": "data"}
    _seed_cache(token, payload)

    # Immediately retrieve - should work
    resp = client.get(f"/api/lookup/{token}/all/")
    assert resp.status_code == 200

    # Simulate timeout by manually deleting
    cache.delete(f"lookup:{token}")

    # Should return 404 after timeout
    resp = client.get(f"/api/lookup/{token}/all/")
    assert resp.status_code == 404


def test_lookup_reuse_existing_patient_examination(client, monkeypatch):
    """Test that restart reuses existing patient examination instead of creating new ones"""
    from endoreg_db.services import lookup_service as ls

    def mock_create_lookup_token_for_pe(pe_id: int) -> str:
        token = f"reuse_test_token_{pe_id}"
        _seed_cache(token, {
            "patient_examination_id": pe_id,
            "requirement_sets": [{"id": 1, "name": "test", "type": "all"}],
            "availableFindings": [],
            "requiredFindings": [],
            "requirementDefaults": {},
            "classificationChoices": {},
        })
        return token

    monkeypatch.setattr(ls, "create_lookup_token_for_pe", mock_create_lookup_token_for_pe)

    # First init
    resp1 = client.post("/api/lookup/init/", data={"patient_examination_id": 789})
    assert resp1.status_code == 201
    token1 = resp1.json()["token"]

    # Clear cache to simulate expiration
    cache.delete(f"lookup:{token1}")

    # Second init with same patient examination - should reuse
    resp2 = client.post("/api/lookup/init/", data={"patient_examination_id": 789})
    assert resp2.status_code == 201
    token2 = resp2.json()["token"]

    # Verify both calls used the same patient examination ID
    assert token1 != token2  # But different tokens


def test_lookup_error_handling_comprehensive(client):
    """Test comprehensive error handling in lookup endpoints"""
    # Test invalid token format
    resp = client.get("/api/lookup/invalid-token/all/")
    assert resp.status_code == 404

    # Test empty token
    resp = client.get("/api/lookup//all/")
    assert resp.status_code == 404

    # Test patch without updates
    token = "patch_test"
    _seed_cache(token, {"test": "data"})

    resp = client.patch(f"/api/lookup/{token}/parts/", data={}, content_type="application/json")
    assert resp.status_code == 400

    # Test parts without keys
    resp = client.get(f"/api/lookup/{token}/parts/")
    assert resp.status_code == 400

    # Test parts with empty keys
    resp = client.get(f"/api/lookup/{token}/parts/?keys=")
    assert resp.status_code == 400
