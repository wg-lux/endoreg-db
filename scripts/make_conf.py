from pathlib import Path
from endoreg_db.utils import DbConfig
from endoreg_db.utils.file_operations import ensure_directory

TEMPLATE_DIR = Path("./conf_template")
CONF_DIR = Path("./conf")

DB_CFG_PATH = TEMPLATE_DIR / "db.yaml"

CONF_TARGETS = {
    "root": CONF_DIR,
    "db": CONF_DIR / "db.yaml",
}


def main(conf_dir: Path = CONF_TARGETS["root"], template_dir: Path = TEMPLATE_DIR):
    db_template = template_dir / "db.yaml"
    assert db_template.exists(), f"Missing Template {DB_CFG_PATH}"

    if not conf_dir.exists():
        ensure_directory(conf_dir)

    if not CONF_TARGETS["db"].exists():
        db_cfg = DbConfig.from_file(DB_CFG_PATH)
        db_cfg.custom_validate()
        db_cfg.to_file(str(CONF_TARGETS["db"]), ask_override=True)


if __name__ == "__main__":
    main()
