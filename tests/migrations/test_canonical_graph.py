from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


def test_canonical_migration_graph_fresh_install(tmp_path: Path) -> None:
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
print(json.dumps({"conflicts": conflicts, "leaf_names": leaf_names, "unapplied": unapplied}))
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        check=True,
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "CANONICAL_MIGRATION_TEST_DB": str(database_path),
        },
    )
    payload = json.loads(result.stdout.strip().splitlines()[-1])

    assert payload == {
        "conflicts": {},
        "leaf_names": ["0073_upload_job_cleanup_receipt"],
        "unapplied": [],
    }
