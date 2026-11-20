from pathlib import Path
from types import SimpleNamespace

import pytest
from django.contrib.auth.models import AnonymousUser, User
from rest_framework.test import APIRequestFactory, force_authenticate

from endoreg_db.services import video_masks
from endoreg_db.views.video.masks import VideoMasksView


pytestmark = pytest.mark.no_db


@pytest.mark.parametrize("method", ["get", "post"])
def test_catalogue_requires_authentication(method: str) -> None:
    request = getattr(APIRequestFactory(), method)(
        "/media/videos/masks/", {}, format="json"
    )
    request.user = AnonymousUser()
    response = VideoMasksView.as_view()(request)
    assert response.status_code in (401, 403)


def test_catalogue_create_list_invalid_duplicate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, settings: object
) -> None:
    monkeypatch.setattr(
        video_masks, "get_runtime_paths", lambda: SimpleNamespace(terminology=tmp_path)
    )
    monkeypatch.setattr("endoreg_db.authz.permissions.is_debug_mode", lambda: True)
    factory = APIRequestFactory()
    payload = {
        "name": "room_2",
        "label": "Room 2",
        "device_name": "Test device",
        "mask": {
            "x": 10,
            "y": 0,
            "width": 80,
            "height": 100,
            "image_width": 100,
            "image_height": 100,
        },
    }
    user = User(username="mask-author")
    for data, status in [
        (payload, 201),
        (payload, 409),
        ({**payload, "name": "../escape"}, 400),
    ]:
        request = factory.post("/media/videos/masks/", data, format="json")
        force_authenticate(request, user=user)
        assert VideoMasksView.as_view()(request).status_code == status
    request = factory.get("/media/videos/masks/")
    force_authenticate(request, user=user)
    response = VideoMasksView.as_view()(request)
    assert any(mask["name"] == "room_2" for mask in response.data["masks"])


def test_read_only_user_cannot_create_masks(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("endoreg_db.authz.permissions.is_debug_mode", lambda: False)

    def roles(*args: object, **kwargs: object) -> list[str]:
        return ["data:read"]

    monkeypatch.setattr(User, "groups", SimpleNamespace(values_list=roles))
    request = APIRequestFactory().post("/media/videos/masks/", {}, format="json")
    force_authenticate(request, user=User(username="reader"))
    assert VideoMasksView.as_view()(request).status_code == 403
