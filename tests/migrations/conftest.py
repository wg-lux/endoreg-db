"""Keep historical-schema tests out of the shared current test schema."""

from collections.abc import Iterator
from uuid import uuid4

import pytest
from django.db import connection


@pytest.fixture(autouse=True)
def isolated_migration_schema(transactional_db: None) -> Iterator[None]:
    assert connection.vendor == "postgresql"
    original_tables = connection.introspection.table_names()
    schema = connection.ops.quote_name(f"migration_test_{uuid4().hex}")
    with connection.cursor() as cursor:
        cursor.execute("SHOW search_path")
        original_path = cursor.fetchone()[0]
        cursor.execute(f"CREATE SCHEMA {schema}")
        cursor.execute(f"SET search_path TO {schema}")
    try:
        yield
    finally:
        # Restore the connection even if migrating back to the leaf failed.
        # No public-schema fallback is present while historical models run.
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT set_config('search_path', %s, false)", [original_path]
            )
            cursor.execute(f"DROP SCHEMA {schema} CASCADE")
        assert connection.introspection.table_names() == original_tables
