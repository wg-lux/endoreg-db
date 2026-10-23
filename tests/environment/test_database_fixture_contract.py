"""Intentionally unmarked tests: default DB access must work in isolation."""

import pytest
from django.conf import settings
from django.contrib.auth.models import User
from django.db import connection


@pytest.mark.parametrize("iteration", range(2))
def test_implicit_database_is_created_and_each_case_rolls_back(iteration: int) -> None:
    with connection.cursor() as cursor:
        cursor.execute("SELECT current_database()")
        assert cursor.fetchone() == (settings.DATABASES["default"]["TEST"]["NAME"],)
    assert not User.objects.filter(username="fixture-isolation-contract").exists()
    User.objects.create(
        username="fixture-isolation-contract", first_name=str(iteration)
    )


@pytest.mark.no_db
def test_explicit_no_database_marker_blocks_queries() -> None:
    with pytest.raises(RuntimeError, match="Database access not allowed"):
        User.objects.exists()
