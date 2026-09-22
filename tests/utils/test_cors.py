from __future__ import annotations

from types import SimpleNamespace

from pytest import MonkeyPatch

from endoreg_db.utils import cors


def _set_allowed_origins(
    monkeypatch: MonkeyPatch,
    origins: list[str],
) -> None:
    def configured_origins() -> list[str]:
        return origins

    monkeypatch.setattr(cors, "get_django_cors_allowed_origins", configured_origins)


def test_resolve_response_origin_returns_none_without_allowed_origins(
    monkeypatch: MonkeyPatch,
) -> None:
    _set_allowed_origins(monkeypatch, [])

    assert (
        cors.resolve_response_origin(
            SimpleNamespace(headers={"Origin": "https://frontend.example"})
        )
        is None
    )


def test_resolve_response_origin_returns_trimmed_allowed_request_origin(
    monkeypatch: MonkeyPatch,
) -> None:
    _set_allowed_origins(
        monkeypatch,
        ["https://fallback.example", "https://frontend.example"],
    )

    assert (
        cors.resolve_response_origin(
            SimpleNamespace(headers={"Origin": " https://frontend.example "})
        )
        == "https://frontend.example"
    )


def test_resolve_response_origin_falls_back_for_untrusted_or_invalid_headers(
    monkeypatch: MonkeyPatch,
) -> None:
    _set_allowed_origins(monkeypatch, ["https://fallback.example"])

    assert (
        cors.resolve_response_origin(
            SimpleNamespace(headers={"Origin": "https://untrusted.example"})
        )
        == "https://fallback.example"
    )
    assert (
        cors.resolve_response_origin(SimpleNamespace(headers="not-a-mapping"))
        == "https://fallback.example"
    )
