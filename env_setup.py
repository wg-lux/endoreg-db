from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

from django.core.management.utils import get_random_secret_key

from endoreg_db.config.env import (
    DATA_DIR_ENV,
    DEFAULT_DJANGO_SETTINGS_MODULE,
    PROTECTED_MEDIA_ROOT_ENV,
    PROTECTED_ROOT_ENV,
    STORAGE_DIR_ENV,
    build_protected_runtime_env,
)

DEFAULT_DB_PASSWORD = "changeme_in_production"
NIX_VARS_FILE = Path(".devenv-vars.json")
DEFAULT_ENV_TEMPLATE = Path("conf/default.env")
ENV_FILE = Path(".env")


def load_nix_vars(path: Path) -> dict[str, str]:
    if not path.exists():
        print(f"No Nix variables file found at {path}")
        return {}

    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)

    print(f"Loaded Nix variables: {', '.join(sorted(data.keys()))}")
    return dict(data)


def read_env(path: Path) -> tuple[list[str], dict[str, str]]:
    lines: list[str] = []
    values: dict[str, str] = {}

    if not path.exists():
        return lines, values

    with path.open("r", encoding="utf-8") as handle:
        lines = handle.readlines()

    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue

        key, value = stripped.split("=", 1)
        values[key.strip()] = value.strip().strip("'").strip('"')

    return lines, values


def write_env(path: Path, lines: list[str], values: dict[str, str]) -> None:
    seen: set[str] = set()
    output: list[str] = []

    for line in lines:
        stripped = line.strip()

        if not stripped or stripped.startswith("#") or "=" not in stripped:
            output.append(line)
            continue

        key, _old_value = stripped.split("=", 1)
        key = key.strip()

        if key in values:
            output.append(f"{key}={values[key]}\n")
            seen.add(key)
        else:
            output.append(line)

    missing = [(key, value) for key, value in values.items() if key not in seen]

    if missing and output and output[-1].strip():
        output.append("\n")

    for key, value in missing:
        output.append(f"{key}={value}\n")

    with path.open("w", encoding="utf-8") as handle:
        handle.writelines(output)


def ensure_file(path: Path, content: str, *, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

    if path.exists():
        return

    path.write_text(content, encoding="utf-8")
    try:
        os.chmod(path, mode)
    except OSError:
        pass


def ensure_env_file(template: Path, target: Path) -> None:
    if target.exists():
        print(".env file already exists. Updating...")
        return

    if template.exists():
        print(f"Creating .env file from template: {template}")
        shutil.copy(template, target)
    else:
        print(f"No template found at {template}. Creating empty .env.")
        target.write_text("", encoding="utf-8")


def main() -> None:
    nix_vars = load_nix_vars(NIX_VARS_FILE)

    working_dir = Path(
        nix_vars.get("WORKING_DIR") or os.path.abspath(os.getcwd())
    ).resolve()

    conf_dir_raw = nix_vars.get("CONF_DIR", "conf")
    conf_dir = Path(conf_dir_raw)
    if not conf_dir.is_absolute():
        conf_dir = working_dir / conf_dir
    conf_dir = conf_dir.resolve()

    db_pwd_file = conf_dir / "db_pwd"

    home_dir = nix_vars.get("HOME_DIR", os.path.expanduser("~"))

    nix_vars["WORKING_DIR"] = str(working_dir)
    nix_vars["CONF_DIR"] = str(conf_dir)
    nix_vars["HOME_DIR"] = home_dir

    protected_runtime_env = build_protected_runtime_env(
        default_protected_root=working_dir / "data",
        base_dir=working_dir,
        source=nix_vars,
    )

    print(f"Checking configuration directory: {conf_dir}")
    conf_dir.mkdir(parents=True, exist_ok=True)

    print(f"Checking database password file: {db_pwd_file}")
    ensure_file(db_pwd_file, DEFAULT_DB_PASSWORD)
    if db_pwd_file.read_text(encoding="utf-8").strip() == DEFAULT_DB_PASSWORD:
        print(
            f"IMPORTANT: {db_pwd_file} contains the default database password. "
            "Change it for production."
        )

    ensure_env_file(DEFAULT_ENV_TEMPLATE, ENV_FILE)

    lines, existing = read_env(ENV_FILE)
    new_values = dict(existing)

    django_module_from_nix = nix_vars.get("DJANGO_MODULE")
    default_django_settings_module = (
        f"{django_module_from_nix}.settings_dev"
        if django_module_from_nix
        else existing.get("DJANGO_SETTINGS_MODULE", DEFAULT_DJANGO_SETTINGS_MODULE)
    )

    # Secrets: generate only when missing.
    new_values.setdefault("DJANGO_SECRET_KEY", get_random_secret_key())
    new_values.setdefault("DJANGO_SALT", get_random_secret_key())

    # Canonical runtime paths: preserve existing values, only add when missing.
    for key in (
        PROTECTED_ROOT_ENV,
        STORAGE_DIR_ENV,
        DATA_DIR_ENV,
        PROTECTED_MEDIA_ROOT_ENV,
    ):
        if key in protected_runtime_env:
            new_values.setdefault(key, str(protected_runtime_env[key]))

    # Backward-compatible/common path aliases.
    new_values.setdefault("DJANGO_CONF_DIR", str(conf_dir))
    new_values.setdefault("HOME_DIR", str(home_dir))
    new_values.setdefault("WORKING_DIR", str(working_dir))

    new_values.setdefault(
        "DJANGO_DATA_DIR",
        str(working_dir / nix_vars.get("DATA_DIR", "data")),
    )
    new_values.setdefault(
        "DJANGO_IMPORT_DATA_DIR",
        str(working_dir / nix_vars.get("IMPORT_DIR", "data/import")),
    )
    new_values.setdefault(
        "DJANGO_VIDEO_IMPORT_DATA_DIR",
        str(working_dir / nix_vars.get("IMPORT_DIR", "data/import") / "video"),
    )

    # Nix-provided app settings.
    if nix_vars.get("HOST"):
        new_values.setdefault("DJANGO_HOST", str(nix_vars["HOST"]))
    if nix_vars.get("PORT"):
        new_values.setdefault("DJANGO_PORT", str(nix_vars["PORT"]))

    # Django settings variants.
    if django_module_from_nix:
        new_values["DJANGO_SETTINGS_MODULE"] = default_django_settings_module
        new_values.setdefault(
            "DJANGO_SETTINGS_MODULE_PRODUCTION",
            f"{django_module_from_nix}.settings_prod",
        )
        new_values.setdefault(
            "DJANGO_SETTINGS_MODULE_DEVELOPMENT",
            default_django_settings_module,
        )
    else:
        new_values.setdefault("DJANGO_SETTINGS_MODULE", default_django_settings_module)

    # Storage/encryption defaults.
    #
    # Keep this default conservative: canonical raw/report files should go through
    # app-encrypted storage unless deployment explicitly chooses another profile.
    new_values.setdefault("ENDOREG_STORAGE_PROFILE", "app_encrypted")

    # Add this only if your encrypted storage reads its key from this variable.
    # Otherwise rename it to the actual env var used by your storage backend.
    new_values.setdefault("ENDOREG_ENCRYPTION_KEY_FILE", str(conf_dir / "encryption.key"))

    encryption_key_file = Path(new_values["ENDOREG_ENCRYPTION_KEY_FILE"])
    if not encryption_key_file.is_absolute():
        encryption_key_file = working_dir / encryption_key_file

    ensure_file(encryption_key_file, get_random_secret_key(), mode=0o600)

    # Runtime defaults.
    defaults = {
        "TEST_RUN": "False",
        "RUST_BACKTRACE": "1",
        "DJANGO_DEBUG": "True",
        "VIDEO_ALLOW_FPS_FALLBACK": "True",
        "VIDEO_DEFAULT_FPS": "50",
        "DJANGO_FFMPEG_EXTRACT_FRAME_BATCHSIZE": "500",
        "LABEL_VIDEO_SEGMENT_MIN_DURATION_S_FOR_ANNOTATION": "3",
    }

    for key, value in defaults.items():
        new_values.setdefault(key, value)

    write_env(ENV_FILE, lines, new_values)

    print(f"Environment setup script finished. Check {ENV_FILE} and {db_pwd_file}")


if __name__ == "__main__":
    main()