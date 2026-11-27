from pytest import MonkeyPatch

from endoreg_db.utils import paths


def test_cache_reset_survives_public_resolver_override(
    monkeypatch: MonkeyPatch,
) -> None:
    cached_resolver = paths.get_runtime_paths
    resolved = cached_resolver()
    assert cached_resolver.cache_info().currsize == 1

    with monkeypatch.context() as override:
        override.setattr(paths, "get_runtime_paths", lambda: resolved)
        paths.clear_runtime_paths_cache()
        assert cached_resolver.cache_info().currsize == 0

    refreshed = cached_resolver()
    assert refreshed is not resolved
    assert cached_resolver() is refreshed
    assert cached_resolver.cache_info().misses == 1
