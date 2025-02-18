from pathlib import Path

TEST_REPORT_DIR = Path("test_reports")

if not TEST_REPORT_DIR.exists():
    TEST_REPORT_DIR.mkdir()#

TEST_LX_PERMISSION_OUTPUT_PATH = TEST_REPORT_DIR / "lx_permissions.txt"
TEST_LX_CLIENT_TYPE_OUTPUT_PATH = TEST_REPORT_DIR / "lx_client_types.txt"
TEST_LX_CLIENT_TAGS_OUTPUT_PATH = TEST_REPORT_DIR / "lx_client_tags.txt"

## Client Tags
# - model: endoreg_db.lx_client_tag
#   fields:
#     name: "coloreg"
#     description: "Coloreg related clients"

# - model: endoreg_db.lx_client_tag
#   fields:
#     name: "client"
#     description: "General client"

# - model: endoreg_db.lx_client_tag
#   fields:
#     name: "server"
#     description: "General server"

# - model: endoreg_db.lx_client_tag
#   fields:
#     name: "production"
#     description: "Production environment"

# - model: endoreg_db.lx_client_tag
#   fields:
#     name: "development"
#     description: "Development environment"

# - model: endoreg_db.lx_client_tag
#   fields:
#     name: "test"
#     description: "Test environment"

CLIENT_TAG_NAME_PERMISSIONS_TUPLES = [ #TODO Adapt to endoreg_db
    ("client", ["local_base_db_read", "local_base_db_write"]),
    ("server", ["local_base_db_read", "local_base_db_write"]),
    ("production", ["local_base_db_read", "local_base_db_write"]),
    ("development", ["local_base_db_read", "local_base_db_write"]),
    ("test", ["local_base_db_read", "local_base_db_write"]),
    ("coloreg", ["local_base_db_read", "local_base_db_write"]),
]

CLIENT_TYPE_NAME_ABBREVIATION_TUPLES = [
    ("base-client", "c"),
    ("gpu-client", "gc"),
    ("coloreg-client", "cc"),
    ("base-server", "s"),
    ("gpu-server", "gs"),
    ("storage-server", "ss"),
]

BASE_PERMISSION_NAMES = [
    "local_admin",
    "local_user",
    "local_base_db_read",
    "local_base_db_write",
]


# EndoReg Permission Names
ENDOREG_PERMISSION_NAMES = [
    "endoreg_base_db_read",
    "endoreg_base_db_write",
    "endoreg_base_db_admin",
    "endoreg_base_db_user",

    "endoreg_backup_db_read",
    "endoreg_backup_db_write",
    "endoreg_backup_db_admin",
    "endoreg_backup_db_user",

    "endoreg_test_db_read",
    "endoreg_test_db_write",
    "endoreg_test_db_admin",
    "endoreg_test_db_user",
]
