import os

DEBUG = os.getenv("DEBUG", "false").lower() == "true"

def get_env_var(var_name: str, default: str = None) -> str | None:
    """
    Get the value of an environment variable, with an optional default value.
    If the environment variable is set, we need to remove flanking quotation marks and spaces.
    if the environment variable is not set, we set it to the default value.
    :param var_name: The name of the environment variable.
    :param default: The default value to return if the environment variable is not set.
    :return: The value of the environment variable or the default value.
    """
    value = os.environ.get(var_name)
    if value:
        value = value.strip('"\'')  # Strip both single and double quotes
        if DEBUG:
            print(f"Environment variable {var_name}: {value}")
        return value
    return default

def set_env_var(var_name: str, value: str) -> None:
    """
    Set the value of an environment variable.
    :param var_name: The name of the environment variable.
    :param value: The value to set.
    """
    os.environ[var_name] = value
    if DEBUG:
        print(f"Set environment variable {var_name}: {value}")

DJANGO_SETTINGS_MODULE = get_env_var("DJANGO_SETTINGS_MODULE") or "endoreg_db.settings_dev"

