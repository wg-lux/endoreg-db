from importlib import import_module
from typing import Generator, Sequence

import pytest
from django.db import IntegrityError, models
from django.db.backends.base.base import BaseDatabaseWrapper
from django.db.migrations.state import ModelState, ProjectState
from django.db.utils import ConnectionHandler

migration_module = import_module(
    "endoreg_db.migrations.0083_repair_raw_video_hash_column"
)


def _state(*hash_columns: str) -> ProjectState:
    state = ProjectState()
    state.add_model(
        ModelState(
            "endoreg_db",
            "VideoFile",
            [
                ("id", models.AutoField(primary_key=True)),
                *[
                    (name, models.CharField(max_length=255, unique=True))
                    for name in hash_columns
                ],
            ],
        )
    )
    return state


@pytest.fixture
def isolated_connection() -> Generator[BaseDatabaseWrapper, None, None]:
    handler = ConnectionHandler(
        {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}}
    )
    connection = handler["default"]
    try:
        yield connection
    finally:
        connection.close()


@pytest.mark.parametrize("column", ["video_hash", "raw_video_hash"])
def test_hash_repair_preserves_values_uniqueness_and_is_idempotent(
    isolated_connection: BaseDatabaseWrapper, column: str
) -> None:
    connection = isolated_connection
    with connection.schema_editor() as editor:
        editor.create_model(_state(column).apps.get_model("endoreg_db", "VideoFile"))
    with connection.cursor() as cursor:
        cursor.execute(
            f'INSERT INTO endoreg_db_videofile ("{column}") VALUES (%s)',
            ["unchanged-hash"],
        )
    canonical_apps = _state("raw_video_hash").apps
    for _ in range(2):
        with connection.schema_editor() as editor:
            migration_module.repair_raw_video_hash_column(canonical_apps, editor)
    with connection.cursor() as cursor:
        cursor.execute("SELECT id, raw_video_hash FROM endoreg_db_videofile")
        assert cursor.fetchall() == [(1, "unchanged-hash")]
        columns = {
            col.name
            for col in connection.introspection.get_table_description(
                cursor, "endoreg_db_videofile"
            )
        }
        assert "video_hash" not in columns
        with pytest.raises(IntegrityError):
            cursor.execute(
                "INSERT INTO endoreg_db_videofile (raw_video_hash) VALUES (%s)",
                ["unchanged-hash"],
            )


@pytest.mark.parametrize("columns", [("video_hash", "raw_video_hash"), ()])
def test_hash_repair_rejects_ambiguous_or_missing_columns(
    isolated_connection: BaseDatabaseWrapper, columns: Sequence[str]
) -> None:
    with isolated_connection.schema_editor() as editor:
        editor.create_model(_state(*columns).apps.get_model("endoreg_db", "VideoFile"))
    with pytest.raises(RuntimeError, match="manual schema review"):
        with isolated_connection.schema_editor() as editor:
            migration_module.repair_raw_video_hash_column(
                _state("raw_video_hash").apps, editor
            )
    with isolated_connection.cursor() as cursor:
        actual = {
            col.name
            for col in isolated_connection.introspection.get_table_description(
                cursor, "endoreg_db_videofile"
            )
        }
    assert actual == {"id", *columns}


def test_migration_apply_and_reverse_keep_canonical_state(
    isolated_connection: BaseDatabaseWrapper,
) -> None:
    connection = isolated_connection
    before = _state("raw_video_hash")
    migration = migration_module.Migration(
        "0083_repair_raw_video_hash_column", "endoreg_db"
    )
    with connection.schema_editor() as editor:
        editor.create_model(
            _state("video_hash").apps.get_model("endoreg_db", "VideoFile")
        )
    with connection.schema_editor() as editor:
        after = migration.apply(before, editor)
    assert set(after.models["endoreg_db", "videofile"].fields) == {
        "id",
        "raw_video_hash",
    }
    with connection.schema_editor() as editor:
        migration.unapply(before, editor)
    with connection.cursor() as cursor:
        columns = {
            col.name
            for col in connection.introspection.get_table_description(
                cursor, "endoreg_db_videofile"
            )
        }
    assert columns == {"id", "raw_video_hash"}
