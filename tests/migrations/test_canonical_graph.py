from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


def test_canonical_migration_graph_fresh_install(tmp_path: Path) -> None:
    migration_marker = (
        Path(__file__).resolve().parents[2]
        / "endoreg_db"
        / "migrations"
        / "max_migration.txt"
    )
    expected_leaf = migration_marker.read_text(encoding="utf-8").strip()
    database_path = tmp_path / "canonical.sqlite3"
    script = """
import json
import os
import secrets

from django.conf import settings

settings.configure(
    SECRET_KEY=secrets.token_urlsafe(32),
    INSTALLED_APPS=[
        "django.contrib.auth",
        "django.contrib.contenttypes",
        "endoreg_db",
    ],
    DATABASES={
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": os.environ["CANONICAL_MIGRATION_TEST_DB"],
        }
    },
    DEFAULT_AUTO_FIELD="django.db.models.BigAutoField",
    USE_TZ=True,
)

import django
from endoreg_db.utils.paths import get_runtime_paths

get_runtime_paths().ensure_directories()
django.setup()

from django.core.management import call_command
from django.db import connection
from django.db.migrations.loader import MigrationLoader

loader = MigrationLoader(connection, ignore_no_migrations=True)
conflicts = loader.detect_conflicts()
leaf_names = sorted(name for app, name in loader.graph.leaf_nodes("endoreg_db"))
call_command("migrate", "endoreg_db", interactive=False, verbosity=0)
loader = MigrationLoader(connection, ignore_no_migrations=True)
unapplied = sorted(
    name
    for app, name in loader.graph.nodes
    if app == "endoreg_db" and (app, name) not in loader.applied_migrations
)
with connection.cursor() as cursor:
    playback_columns = [
        column.name
        for column in connection.introspection.get_table_description(cursor, "endoreg_db_videofile")
        if column.name.startswith("optimized_playback")
    ]
print(json.dumps({"conflicts": conflicts, "leaf_names": leaf_names, "unapplied": unapplied,
                  "playback_columns": playback_columns}))
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        check=False,
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "CANONICAL_MIGRATION_TEST_DB": str(database_path),
            "LX_RUNTIME_ROOT": str(tmp_path / "runtime"),
        },
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout.strip().splitlines()[-1])

    assert payload == {
        "conflicts": {},
        "leaf_names": [expected_leaf],
        "unapplied": [],
        "playback_columns": [],
    }
