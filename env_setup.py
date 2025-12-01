from django.core.management.utils import get_random_secret_key
import os
import json
from pathlib import Path
import shutil


# --- Constants ---
DEFAULT_DB_PASSWORD = "changeme_in_production" # Placeholder password

# --- Load Nix Variables ---
nix_vars = {}
nix_vars_path = Path("./.devenv-vars.json")
if nix_vars_path.exists():
    with open(nix_vars_path, 'r', encoding="utf-8") as f:
        nix_vars = json.load(f)
    print(f"Loaded Nix variables: {', '.join(nix_vars.keys())}")
else:
    print("No Nix variables file found at .devenv-vars.json")

# --- Determine Paths ---
# Use WORKING_DIR from Nix vars or fallback to os.getcwd()
working_dir_str = nix_vars.get("WORKING_DIR", os.path.abspath(os.getcwd()))
working_dir = Path(working_dir_str)

# Use CONF_DIR from Nix vars or fallback to default relative path
conf_dir_rel = nix_vars.get("CONF_DIR", "conf")
conf_dir = (working_dir / conf_dir_rel).resolve()
db_pwd_file = conf_dir / "db_pwd"

# Update nix_vars with resolved absolute paths for consistency if needed elsewhere
nix_vars["WORKING_DIR"] = str(working_dir)
nix_vars["CONF_DIR"] = str(conf_dir) # Store absolute conf path
home_dir = nix_vars.get("HOME_DIR", os.path.expanduser("~")) # Keep home_dir logic
nix_vars["HOME_DIR"] = home_dir

# --- Generate Secrets ---
SALT = get_random_secret_key()
SECRET_KEY = get_random_secret_key()

# --- Ensure conf dir and db_pwd file exist ---
print(f"Checking configuration directory: {conf_dir}")
if not conf_dir.exists():
    print(f"Creating configuration directory: {conf_dir}")
    conf_dir.mkdir(parents=True, exist_ok=True)
else:
    print("Configuration directory already exists.")

print(f"Checking database password file: {db_pwd_file}")
if not db_pwd_file.exists():
    print(f"Database password file not found. Creating '{db_pwd_file}' with default password.")
    try:
        with open(db_pwd_file, 'w', encoding='utf-8') as f:
            f.write(DEFAULT_DB_PASSWORD)
        print(f"Successfully created '{db_pwd_file}'. IMPORTANT: Change the default password for production!")
    except IOError as e:
        print(f"ERROR: Failed to create database password file '{db_pwd_file}': {e}")
else:
    print("Database password file already exists.")


# --- Manage .env file ---
template = Path("./conf/default.env")
target = Path(".env") # .env should be in the working_dir (project root)

# Create a new .env file from template if it doesn't exist
if not target.exists():
    print(f"Creating .env file from template: {template}")
    try:
        shutil.copy(template, target)
    except Exception as e:
        print(f"Error copying template {template} to {target}: {e}")
else:
    print(".env file already exists. Updating...")

# Track what we've found or added in .env
found_keys = set()

# Read existing entries from .env
lines = []
if target.exists():
    try:
        with target.open("r", encoding="utf-8") as f:
            lines = f.readlines()
    except IOError as e:
        print(f"Error reading .env file {target}: {e}")


# Process and update entries
updated_lines = []
django_module_from_nix = nix_vars.get("DJANGO_MODULE")

for line in lines:
    stripped_line = line.strip()
    if not stripped_line or stripped_line.startswith("#"):
        updated_lines.append(line)
        continue

    if "=" not in stripped_line:
        updated_lines.append(line)
        continue

    key, value = stripped_line.split("=", 1)
    key = key.strip()
    found_keys.add(key)

    # Replace specific values from nix_vars if present, without quotes
    if django_module_from_nix:
        if key == "DJANGO_SETTINGS_MODULE":
            updated_lines.append(f'{key}={django_module_from_nix}.settings_dev\n')
            continue
        elif key == "DJANGO_SETTINGS_MODULE_PRODUCTION":
            updated_lines.append(f'{key}={django_module_from_nix}.settings_prod\n')
            continue
        elif key == "DJANGO_SETTINGS_MODULE_DEVELOPMENT":
            updated_lines.append(f'{key}={django_module_from_nix}.settings_dev\n')
            continue

    # Keep existing line if no specific update rule matched
    updated_lines.append(line)


# Write updated content back to .env
try:
    with target.open("w", encoding="utf-8") as f:
        f.writelines(updated_lines)
except IOError as e:
    print(f"Error writing updated .env file {target}: {e}")

# Add any missing required entries to .env without quotes
try:
    with target.open("a", encoding="utf-8") as f:
        # Add secrets if missing
        if "DJANGO_SECRET_KEY" not in found_keys:
            f.write(f'\nDJANGO_SECRET_KEY={SECRET_KEY}') # No quotes
            print("Added DJANGO_SECRET_KEY to .env")

        if "DJANGO_SALT" not in found_keys:
            f.write(f'\nDJANGO_SALT={SALT}') # No quotes
            print("Added DJANGO_SALT to .env")
        
        # Add Storage_DIR if missing
        if "STORAGE_DIR" not in found_keys:
            storage_dir = nix_vars.get("STORAGE_DIR", str(working_dir / "storage"))
            f.write(f'\nSTORAGE_DIR={storage_dir}')
            print("Added STORAGE_DIR to .env")

        # Add paths and config from nix_vars if missing
        # Ensure paths are NOT quoted
        vars_to_add = {
            "DJANGO_HOST": nix_vars.get("HOST"),
            "DJANGO_PORT": nix_vars.get("PORT"),
            "DJANGO_CONF_DIR": str(conf_dir),
            "HOME_DIR": nix_vars.get("HOME_DIR"),
            "WORKING_DIR": nix_vars.get("WORKING_DIR"),
            "DJANGO_DATA_DIR": str(working_dir / nix_vars.get("DATA_DIR", "data")),
            "DJANGO_IMPORT_DATA_DIR": str(working_dir / nix_vars.get("IMPORT_DIR", "data/import")),
            "DJANGO_VIDEO_IMPORT_DATA_DIR": str(working_dir / nix_vars.get("IMPORT_DIR", "data/import") / "video"),
            "VIDEO_ALLOW_FPS_FALLBACK": "True",
            "VIDEO_DEFAULT_FPS": "50",
        }
        for key, value in vars_to_add.items():
            if value is not None and key not in found_keys:
                f.write(f'\n{key}={value}') # No quotes
                print(f"Added {key} to .env")

        # Add Django settings module variants if missing and module name is known
        if django_module_from_nix:
            settings_variants = {
                "DJANGO_SETTINGS_MODULE": f"{django_module_from_nix}.settings_dev",
                "DJANGO_SETTINGS_MODULE_PRODUCTION": f"{django_module_from_nix}.settings_prod",
                "DJANGO_SETTINGS_MODULE_DEVELOPMENT": f"{django_module_from_nix}.settings_dev",
            }
            for key, value in settings_variants.items():
                if key not in found_keys:
                    f.write(f'\n{key}={value}') # No quotes
                    print(f"Added {key} to .env")

        # Add other defaults if missing
        default_values = {
            "TEST_RUN": "False",
            "TEST_RUN_FRAME_NUMBER": "1000",
            "RUST_BACKTRACE": "1",
            "DJANGO_DEBUG": "True",
            "DJANGO_FFMPEG_EXTRACT_FRAME_BATCHSIZE": "500",
            "LABEL_VIDEO_SEGMENT_MIN_DURATION_S_FOR_ANNOTATION": "3" # Added missing default
        }
        for key, value in default_values.items():
            if key not in found_keys:
                f.write(f'\n{key}={value}') # No quotes
                print(f"Added {key} to .env")

except IOError as e:
    print(f"Error appending missing entries to .env file {target}: {e}")


print(f"Environment setup script finished. Check {target} and {db_pwd_file}")
