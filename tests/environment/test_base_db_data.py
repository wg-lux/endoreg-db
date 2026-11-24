from __future__ import annotations

import pytest
from django.core.management import call_command

from endoreg_db.models import Center, Gender
from tests.conftest import ensure_base_db_data
from tests.helpers.default_objects import (
    DEFAULT_CENTER_NAME,
    get_default_center,
    get_default_processor,
)
from tests.plugins.cache import CacheManager


def _assert_base_data_present() -> None:
    assert get_default_center().pk is not None
    assert get_default_processor().pk is not None
    assert Gender.objects.filter(name="female").exists()


@pytest.mark.django_db(transaction=True)
def test_base_db_data_reloads_after_transaction_flush(
    base_db_data: bool,
    cache: CacheManager,
) -> None:
    """A cached readiness flag must not outlive the rows it describes."""
    assert base_db_data
    _assert_base_data_present()

    call_command("flush", interactive=False, verbosity=0)
    assert not Center.objects.filter(name=DEFAULT_CENTER_NAME).exists()

    assert ensure_base_db_data(cache)
    _assert_base_data_present()
