from pathlib import Path
from typing import Optional, Union
import yaml
from icecream import ic
from pydantic import BaseModel


class DbConfig(BaseModel):
    """Database configuration model."""

    engine: str = "django.db.backends.postgresql"
    host: str
    port: int
    user: str
    password: Optional[str] = None
    password_file: str = "/etc/secrets/vault/SCRT_local_password_maintenance_password"
    name: str

    # class options should allow for unused excess data

    @classmethod
    def from_file(cls, path: Union[Path, str]) -> "DbConfig":
        """Create a DbConfig instance from a YAML configuration file."""
        if isinstance(path, str):
            filepath = Path(path)
        else:
            filepath = path
        assert filepath.exists(), f"Missing Config {filepath}"

        with open(filepath, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f)

        obj = cls(**cfg)

        return obj

    def sync_password(self):
        """Load the password from the configured password file."""
        with open(self.password_file, "r", encoding="utf-8") as f:
            self.password = f.read().strip()

    def custom_validate(self):
        """Fully validate all database configuration fields."""
        self.sync_password()

        assert self.host, "Missing Host"
        assert self.port, "Missing Port"
        assert self.user, "Missing User"
        assert self.password, "Missing Password"
        assert self.name, "Missing Database"

    def to_file(self, target: str = "./conf/db.yml", ask_override: bool = True):
        """Export the configuration to a YAML file."""
        ic(target)

        with open(target, "w") as f:
            yaml.safe_dump(self.model_dump(), f)
