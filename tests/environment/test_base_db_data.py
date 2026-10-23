from __future__ import annotations

import pytest

from endoreg_db.models import Gender
from tests.helpers.default_objects import get_default_center, get_default_processor


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("iteration", range(2))
def test_base_db_data_survives_transaction_flush(
    base_db_data: bool,
    iteration: int,
) -> None:
    """Each parameter case flushes the database before the next fixture request."""
    assert base_db_data
    assert get_default_center().pk is not None
    assert get_default_processor().pk is not None
    assert Gender.objects.filter(name="female").exists()
