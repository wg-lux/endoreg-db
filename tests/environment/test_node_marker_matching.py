from __future__ import annotations

from types import SimpleNamespace
from typing import cast

import pytest

from tests.conftest import _node_has_word  # pyright: ignore[reportPrivateUsage]

pytestmark = pytest.mark.no_db

# Explicit ids keep the sample node ids out of these tests' own node ids, which
# the matcher under test would otherwise read as marker words.
MARKER_WORDS = ("ai", "inference")


def _item(nodeid: str) -> pytest.Item:
    return cast(pytest.Item, SimpleNamespace(nodeid=nodeid))


@pytest.mark.parametrize(
    "nodeid",
    [
        pytest.param("tests/services/test_ai_model.py::test_load", id="module-word"),
        pytest.param("tests/x.py::TestAIModel::test_load", id="camel-acronym"),
        pytest.param("tests/x.py::TestInferenceRunner::test_run", id="camel-word"),
        pytest.param("tests/x.py::test_batch_inference[cpu]", id="snake-word"),
        pytest.param("tests/x.py::test_run[ai-model]", id="parameter-word"),
    ],
)
def test_whole_marker_words_are_matched(nodeid: str) -> None:
    assert _node_has_word(_item(nodeid), *MARKER_WORDS)


@pytest.mark.parametrize(
    "nodeid",
    [
        pytest.param("tests/x.py::test_failed_attempt_is_retried", id="failed"),
        pytest.param("tests/x.py::test_import_claim_retry_paths", id="claim"),
        pytest.param("tests/x.py::TestPdf::test_when_available", id="available"),
        pytest.param("tests/x.py::TestDetailView::test_contains_again", id="detail"),
        pytest.param("tests/x.py::TestMaintainer::test_chain", id="maintainer"),
    ],
)
def test_marker_substrings_inside_words_are_not_matched(nodeid: str) -> None:
    assert not _node_has_word(_item(nodeid), *MARKER_WORDS)
