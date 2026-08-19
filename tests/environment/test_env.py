import importlib
import json
import logging
import os
from pathlib import Path
import subprocess
import sys
from typing import Callable

import pytest
from django.test import TestCase

from endoreg_db.config import env as env_module

logger = logging.getLogger(__name__)

TEST_ENV_KEY = "ENDOREG_TEST_TYPED_ENV_VALUE"
REPO_ROOT = Path(__file__).resolve().parents[2]


def _run_dotenv_import_probe(
    tmp_path: Path,
    *,
    settings_module: str,
) -> subprocess.CompletedProcess[str]:
    stub_root = tmp_path / "dotenv_stub"
    stub_root.mkdir()
    (stub_root / "dotenv.py").write_text(
        "def load_dotenv(*, override=False):\n"
        "    print(f'dotenv-stub-loaded:{override}')\n"
        "    return True\n",
        encoding="utf-8",
    )
    environment = os.environ.copy()
    environment.pop("PYTEST_CURRENT_TEST", None)
    environment["DJANGO_SETTINGS_MODULE"] = settings_module
    inherited_python_path = environment.get("PYTHONPATH")
    python_path_parts = [str(stub_root), str(REPO_ROOT)]
    if inherited_python_path:
        python_path_parts.append(inherited_python_path)
    environment["PYTHONPATH"] = os.pathsep.join(python_path_parts)
    probe = (
        "import json\n"
        "from endoreg_db.config import env\n"
        "print(json.dumps({'dotenv_loaded': env.snapshot()['DOTENV_LOADED']}))\n"
    )
    return subprocess.run(
        [sys.executable, "-c", probe],
        cwd=tmp_path,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )


@pytest.mark.parametrize(
    "settings_module",
    [
        "endoreg_db.config.settings.test",
        "endoreg_db.config.settings.prod",
    ],
)
def test_dotenv_is_not_imported_outside_endoreg_development_settings(
    tmp_path: Path,
    settings_module: str,
) -> None:
    result = _run_dotenv_import_probe(tmp_path, settings_module=settings_module)

    assert result.returncode == 0, result.stderr
    assert "dotenv-stub-loaded" not in result.stdout
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    assert payload == {"dotenv_loaded": False}


def test_dotenv_loading_remains_available_for_endoreg_development(
    tmp_path: Path,
) -> None:
    result = _run_dotenv_import_probe(
        tmp_path,
        settings_module="endoreg_db.config.settings.dev",
    )

    assert result.returncode == 0, result.stderr
    assert "dotenv-stub-loaded:False" in result.stdout
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    assert payload == {"dotenv_loaded": True}


def test_environment_snapshot_redacts_credentials_and_topology(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    broker_url = "rediss://runtime-user:sensitive-password@broker.internal/0"
    storage_path = "/sensitive/internal/storage/location"
    monkeypatch.setenv("CELERY_BROKER_URL", broker_url)
    monkeypatch.setenv("STORAGE_DIR", storage_path)
    monkeypatch.setenv("MODEL_TRAINING_STAGING_ROOT", "/sensitive/training")
    monkeypatch.setenv("CELERY_RUNTIME_CONFIG_STRICT", "true")

    result = env_module.snapshot()
    serialized = json.dumps(result, sort_keys=True)

    assert result["CELERY_BROKER_URL"] == env_module.SNAPSHOT_REDACTED_VALUE
    assert result["STORAGE_DIR"] == env_module.SNAPSHOT_REDACTED_VALUE
    assert result["MODEL_TRAINING_STAGING_ROOT"] == env_module.SNAPSHOT_REDACTED_VALUE
    assert result["BASE_DIR"] == env_module.SNAPSHOT_REDACTED_VALUE
    assert result["CELERY_RUNTIME_CONFIG_STRICT"] == "true"
    assert broker_url not in serialized
    assert storage_path not in serialized
    assert "sensitive-password" not in serialized


def test_typed_env_helpers_use_defaults_only_when_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(TEST_ENV_KEY, raising=False)

    assert env_module.env_bool(TEST_ENV_KEY, True) is True
    assert env_module.env_int(TEST_ENV_KEY, 17) == 17
    assert env_module.env_float(TEST_ENV_KEY, 2.5) == 2.5


def test_env_path_uses_default_only_when_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(TEST_ENV_KEY, raising=False)

    assert (
        env_module.env_path(TEST_ENV_KEY, "data/default")
        == (REPO_ROOT / "data" / "default").resolve()
    )


@pytest.mark.parametrize("raw_value", ["", " ", "\t\n"])
def test_env_path_rejects_explicit_empty_value(
    monkeypatch: pytest.MonkeyPatch,
    raw_value: str,
) -> None:
    monkeypatch.setenv(TEST_ENV_KEY, raw_value)

    with pytest.raises(env_module.EnvironmentValueError) as error:
        env_module.env_path(TEST_ENV_KEY, "data/default")

    assert error.value.key == TEST_ENV_KEY
    assert error.value.expected == "a non-empty filesystem path"


def test_env_choice_uses_default_only_when_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    choices = ("celery", "inline")
    monkeypatch.delenv(TEST_ENV_KEY, raising=False)
    assert env_module.env_choice(TEST_ENV_KEY, choices, "inline") == "inline"

    monkeypatch.setenv(TEST_ENV_KEY, " CeLeRy ")
    assert env_module.env_choice(TEST_ENV_KEY, choices, "inline") == "celery"


def test_env_choice_rejects_invalid_value_without_disclosing_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_value = "sensitive-invalid-mode"
    monkeypatch.setenv(TEST_ENV_KEY, raw_value)

    with pytest.raises(env_module.EnvironmentValueError) as error:
        env_module.env_choice(TEST_ENV_KEY, ("celery", "inline"), "celery")

    assert error.value.key == TEST_ENV_KEY
    assert error.value.expected == "one of: celery, inline"
    assert raw_value not in str(error.value)


def test_env_choice_rejects_invalid_helper_contract() -> None:
    with pytest.raises(ValueError, match="choices must not be empty"):
        env_module.env_choice(TEST_ENV_KEY, (), "celery")
    with pytest.raises(ValueError, match="default must be one of"):
        env_module.env_choice(TEST_ENV_KEY, ("celery",), "inline")


@pytest.mark.parametrize(
    ("raw_value", "expected"),
    [
        ("1", True),
        (" true ", True),
        ("YES", True),
        ("On", True),
        ("0", False),
        (" false ", False),
        ("NO", False),
        ("Off", False),
    ],
)
def test_env_bool_accepts_documented_values(
    monkeypatch: pytest.MonkeyPatch,
    raw_value: str,
    expected: bool,
) -> None:
    monkeypatch.setenv(TEST_ENV_KEY, raw_value)

    assert env_module.env_bool(TEST_ENV_KEY, not expected) is expected


def test_env_bool_rejects_invalid_value_without_disclosing_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_value = "sensitive-invalid-boolean"
    monkeypatch.setenv(TEST_ENV_KEY, raw_value)

    with pytest.raises(env_module.EnvironmentValueError) as error:
        env_module.env_bool(TEST_ENV_KEY)

    assert error.value.key == TEST_ENV_KEY
    assert error.value.expected == "a boolean"
    assert TEST_ENV_KEY in str(error.value)
    assert raw_value not in str(error.value)


@pytest.mark.parametrize(
    ("raw_value", "expected"),
    [(" 42 ", 42), ("-7", -7), ("+3", 3)],
)
def test_env_int_accepts_valid_values(
    monkeypatch: pytest.MonkeyPatch,
    raw_value: str,
    expected: int,
) -> None:
    monkeypatch.setenv(TEST_ENV_KEY, raw_value)

    assert env_module.env_int(TEST_ENV_KEY) == expected


@pytest.mark.parametrize(
    ("raw_value", "expected"),
    [(" 3.25 ", 3.25), ("-0.5", -0.5), ("1e3", 1000.0)],
)
def test_env_float_accepts_finite_values(
    monkeypatch: pytest.MonkeyPatch,
    raw_value: str,
    expected: float,
) -> None:
    monkeypatch.setenv(TEST_ENV_KEY, raw_value)

    assert env_module.env_float(TEST_ENV_KEY) == expected


@pytest.mark.parametrize(
    ("parser", "raw_value", "expected_description", "has_cause"),
    [
        (env_module.env_int, "sensitive-invalid-integer", "an integer", True),
        (env_module.env_float, "sensitive-invalid-number", "a finite number", True),
        (env_module.env_float, "nan", "a finite number", False),
        (env_module.env_float, "inf", "a finite number", False),
        (env_module.env_float, "-inf", "a finite number", False),
    ],
)
def test_numeric_env_helpers_reject_invalid_or_non_finite_values(
    monkeypatch: pytest.MonkeyPatch,
    parser: Callable[[str], int | float],
    raw_value: str,
    expected_description: str,
    has_cause: bool,
) -> None:
    monkeypatch.setenv(TEST_ENV_KEY, raw_value)

    with pytest.raises(env_module.EnvironmentValueError) as error:
        parser(TEST_ENV_KEY)

    assert error.value.key == TEST_ENV_KEY
    assert error.value.expected == expected_description
    assert TEST_ENV_KEY in str(error.value)
    assert raw_value not in str(error.value)
    assert (error.value.__cause__ is not None) is has_cause


def test_env_int_rejects_configured_value_below_minimum(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_value = "-17"
    monkeypatch.setenv(TEST_ENV_KEY, raw_value)

    with pytest.raises(env_module.EnvironmentValueError) as error:
        env_module.env_int(TEST_ENV_KEY, minimum=0)

    assert error.value.expected == "an integer greater than or equal to 0"
    assert raw_value not in str(error.value)


@pytest.mark.parametrize(
    ("key", "configured", "getter", "expected"),
    [
        (
            "VIDEO_POST_VALIDATION_JOB_MODE",
            " ThReAd ",
            env_module.get_video_post_validation_job_mode,
            "thread",
        ),
        (
            "VIDEO_TEMPORAL_INFERENCE_JOB_MODE",
            " InLiNe ",
            env_module.get_video_temporal_inference_job_mode,
            "inline",
        ),
        (
            "VIDEO_TEMPORAL_INFERENCE_FRAME_SOURCE_MODE",
            " AuTo ",
            env_module.get_video_temporal_inference_frame_source_mode,
            "auto",
        ),
        (
            "MODEL_TRAINING_JOB_MODE",
            " CeLeRy ",
            env_module.get_model_training_job_mode,
            "celery",
        ),
    ],
)
def test_typed_mode_getters_normalize_supported_values(
    monkeypatch: pytest.MonkeyPatch,
    key: str,
    configured: str,
    getter: Callable[[], str],
    expected: str,
) -> None:
    monkeypatch.setenv(key, configured)

    assert getter() == expected


@pytest.mark.parametrize(
    ("key", "getter"),
    [
        (
            "VIDEO_POST_VALIDATION_JOB_MODE",
            env_module.get_video_post_validation_job_mode,
        ),
        (
            "VIDEO_TEMPORAL_INFERENCE_JOB_MODE",
            env_module.get_video_temporal_inference_job_mode,
        ),
        (
            "VIDEO_TEMPORAL_INFERENCE_FRAME_SOURCE_MODE",
            env_module.get_video_temporal_inference_frame_source_mode,
        ),
        ("MODEL_TRAINING_JOB_MODE", env_module.get_model_training_job_mode),
    ],
)
def test_typed_mode_getters_reject_unsupported_values(
    monkeypatch: pytest.MonkeyPatch,
    key: str,
    getter: Callable[[], str],
) -> None:
    raw_value = "sensitive-unsupported-mode"
    monkeypatch.setenv(key, raw_value)

    with pytest.raises(env_module.EnvironmentValueError) as error:
        getter()

    assert error.value.key == key
    assert raw_value not in str(error.value)


@pytest.mark.parametrize(
    ("key", "configured", "getter"),
    [
        (
            "CELERY_BEAT_AUDIT_LEDGER_INTEGRITY_INTERVAL_SECONDS",
            "59",
            env_module.get_celery_audit_ledger_integrity_interval_seconds,
        ),
        (
            "FFMPEG_TRANSCODE_TIMEOUT_SECONDS",
            "0",
            env_module.get_ffmpeg_transcode_timeout_seconds,
        ),
        (
            "VIDEO_POST_VALIDATION_JOB_MAX_WORKERS",
            "0",
            env_module.get_video_post_validation_job_max_workers,
        ),
        (
            "VIDEO_POST_VALIDATION_DISPATCH_DELAY_SECONDS",
            "-1",
            env_module.get_video_post_validation_dispatch_delay_seconds,
        ),
        (
            "MEDIA_OPERATION_STREAM_LEASE_SECONDS",
            "0",
            env_module.get_media_operation_stream_lease_seconds,
        ),
        (
            "MEDIA_OPERATION_SEGMENT_UPDATE_GRACE_SECONDS",
            "0",
            env_module.get_media_operation_segment_update_grace_seconds,
        ),
    ],
)
def test_bounded_integer_getters_reject_out_of_range_values(
    monkeypatch: pytest.MonkeyPatch,
    key: str,
    configured: str,
    getter: Callable[[], int],
) -> None:
    monkeypatch.setenv(key, configured)

    with pytest.raises(env_module.EnvironmentValueError) as error:
        getter()

    assert error.value.key == key
    assert configured not in str(error.value)


@pytest.mark.parametrize(
    ("key", "configured", "getter", "expected_description"),
    [
        (
            "WATCHER_POLL_INTERVAL_SECONDS",
            "0",
            env_module.get_watcher_poll_interval_seconds,
            "a positive finite number",
        ),
        (
            "WATCHER_POLL_INTERVAL_SECONDS",
            "-0.5",
            env_module.get_watcher_poll_interval_seconds,
            "a positive finite number",
        ),
        (
            "WATCHER_STABLE_AFTER_SECONDS",
            "-0.5",
            env_module.get_watcher_stable_after_seconds,
            "a finite number greater than or equal to 0",
        ),
    ],
)
def test_watcher_duration_getters_reject_unsafe_values(
    monkeypatch: pytest.MonkeyPatch,
    key: str,
    configured: str,
    getter: Callable[[], float],
    expected_description: str,
) -> None:
    monkeypatch.setenv(key, configured)

    with pytest.raises(env_module.EnvironmentValueError) as error:
        getter()

    assert error.value.key == key
    assert error.value.expected == expected_description
    assert configured not in str(error.value)


def test_watcher_stability_window_allows_explicit_zero_for_test_callers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("WATCHER_STABLE_AFTER_SECONDS", "0")

    assert env_module.get_watcher_stable_after_seconds() == 0


@pytest.mark.parametrize("configured", ["true", "1", "YES", "on"])
def test_reconciliation_disabled_accepts_standard_true_values(
    monkeypatch: pytest.MonkeyPatch,
    configured: str,
) -> None:
    monkeypatch.setenv("ENDOREG_DISABLE_RECONCILIATION", configured)

    assert env_module.reconciliation_disabled() is True


def test_video_default_fps_rejects_non_positive_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("VIDEO_DEFAULT_FPS", "0")

    with pytest.raises(env_module.EnvironmentValueError) as error:
        env_module.get_video_default_fps()

    assert error.value.key == "VIDEO_DEFAULT_FPS"
    assert error.value.expected == "a positive finite number"


class TestEnvironment(TestCase):
    """
    Test the environment setup for the project.
    This includes checking if the necessary environment variables are set.
    """

    def test_storage_dir(self):
        """
        Test if the STORAGE_DIR environment variable is set correctly.
        """
        storage_dir = os.environ.get("STORAGE_DIR")
        protected_root = os.environ.get("LX_ANNOTATE_ENCRYPTED_DATA_DIR")
        data_dir = os.environ.get("DATA_DIR")

        self.assertIsNotNone(storage_dir)
        self.assertIsNotNone(protected_root)
        self.assertIsNotNone(data_dir)

        assert storage_dir is not None
        assert protected_root is not None
        assert data_dir is not None
        storage_dir_path = Path(storage_dir).resolve().as_posix()
        protected_root_path = Path(protected_root).resolve().as_posix()
        data_dir_path = Path(data_dir).resolve().as_posix()

        self.assertTrue(
            storage_dir_path.startswith(protected_root_path),
            "STORAGE_DIR must resolve within the protected root.",
        )
        self.assertFalse(
            data_dir_path.startswith(protected_root_path),
            "DATA_DIR should remain separate from the protected root used in tests.",
        )

    def test_core_modules_import(self):
        modules = [
            "endoreg_db.models",
            "endoreg_db.serializers",
            "endoreg_db.views",
            "endoreg_db.utils.paths",
        ]
        for module_name in modules:
            imported = importlib.import_module(module_name)
            assert imported is not None

    def test_env_helpers_define_base_runtime_defaults(self):
        original_user_rate = os.environ.pop("DRF_THROTTLE_USER", None)
        original_anon_rate = os.environ.pop("DRF_THROTTLE_ANON", None)
        try:
            self.assertEqual(
                env_module.get_asset_dir(),
                env_module.BASE_DIR / "tests/assets",
            )
            self.assertEqual(env_module.get_time_zone(), "Europe/Berlin")
            self.assertEqual(env_module.get_static_url(), "/static/")
            self.assertEqual(env_module.get_protected_media_url(), "/protected_media/")
            self.assertEqual(
                env_module.get_protected_media_root(),
                Path(os.environ["PROTECTED_MEDIA_ROOT"]).resolve(),
            )
            self.assertEqual(
                env_module.get_media_root(),
                env_module.get_protected_media_root(),
            )
            self.assertEqual(env_module.get_cache_timeout_seconds(), 60 * 30)
            self.assertEqual(env_module.get_drf_throttle_user_rate(), "100/hour")
            self.assertEqual(env_module.get_drf_throttle_anon_rate(), "20/hour")
        finally:
            if original_user_rate is not None:
                os.environ["DRF_THROTTLE_USER"] = original_user_rate
            if original_anon_rate is not None:
                os.environ["DRF_THROTTLE_ANON"] = original_anon_rate

    def test_endoreg_deployment_role_validation(self):
        original = os.environ.get("ENDOREG_DEPLOYMENT_ROLE")
        try:
            os.environ["ENDOREG_DEPLOYMENT_ROLE"] = "central_hub"
            self.assertEqual(
                env_module.get_endoreg_deployment_role(),
                "central_hub",
            )
            os.environ["ENDOREG_DEPLOYMENT_ROLE"] = "local_study_server"
            self.assertEqual(
                env_module.get_endoreg_deployment_role(),
                "local_study_server",
            )

            os.environ["ENDOREG_DEPLOYMENT_ROLE"] = "invalid-role"
            with self.assertRaises(ValueError):
                env_module.get_endoreg_deployment_role()
        finally:
            if original is None:
                os.environ.pop("ENDOREG_DEPLOYMENT_ROLE", None)
            else:
                os.environ["ENDOREG_DEPLOYMENT_ROLE"] = original
