from __future__ import annotations

import math
import random

import pytest

from endoreg_db.services.video_files.timeline import (
    VideoTimelineMapping,
    VideoTimelineMappingError,
    frame_number_to_seconds,
    seconds_to_frame_number,
)
from endoreg_db.utils.rust_backend import (
    NativeTimelineIndex,
    build_timeline_index,
    compose_frame_ancestry,
)


def _native(
    mapping: VideoTimelineMapping, rows: list[tuple[int, float]]
) -> NativeTimelineIndex:
    return build_timeline_index(
        [frame_number for frame_number, _ in rows],
        [timestamp for _, timestamp in rows],
        fps=mapping.fps,
        frame_count=mapping.frame_count,
        duration_seconds=mapping.duration_seconds,
        requires_persisted_pts=mapping.requires_persisted_pts,
        first_timestamp_seconds=mapping.first_timestamp_seconds,
        content_hash="a" * 64,
    )


def _reference_neighbors(
    rows: list[tuple[int, float]], timestamp: float
) -> dict[int, float]:
    """Mirror the two persisted-neighbor queries in services/video_files/_time.py."""
    before = max(
        (row for row in rows if row[1] <= timestamp),
        key=lambda row: (row[1], row[0]),
        default=None,
    )
    after = min(
        (row for row in rows if row[1] >= timestamp),
        key=lambda row: (row[1], row[0]),
        default=None,
    )
    return {row[0]: row[1] for row in (before, after) if row is not None}


def _irregular_rows(
    *, seed: int, frame_count: int, origin: float
) -> tuple[list[tuple[int, float]], float]:
    generator = random.Random(seed)
    timestamp = origin
    rows: list[tuple[int, float]] = []
    for frame_number in range(frame_count):
        rows.append((frame_number, timestamp))
        timestamp += generator.choice((1 / 50, 1 / 25, 0.0333, 0.07))
    return rows, timestamp - origin


@pytest.mark.unit
@pytest.mark.parametrize("seed", [1, 7, 42])
@pytest.mark.parametrize("origin", [0.0, 2.0, 37.125])
def test_native_index_matches_canonical_rules_for_variable_rate_media(
    seed: int, origin: float
) -> None:
    rows, duration = _irregular_rows(seed=seed, frame_count=60, origin=origin)
    mapping = VideoTimelineMapping(
        fps=25.0,
        frame_count=len(rows),
        duration_seconds=duration,
        requires_persisted_pts=True,
        first_timestamp_seconds=origin,
    )
    persisted = dict(rows)
    native = _native(mapping, rows)

    for frame_number in range(len(rows) + 1):
        expected = frame_number_to_seconds(
            mapping, frame_number, persisted_timestamp=persisted.get(frame_number)
        )
        assert native.frame_number_to_seconds(frame_number) == expected

    generator = random.Random(seed + 1000)
    probes = [timestamp for _, timestamp in rows]
    probes += [(left + right) / 2 for (_, left), (_, right) in zip(rows, rows[1:])]
    probes += [origin + generator.uniform(0, duration) for _ in range(200)]
    probes += [origin + duration, origin + duration + 1e-10]
    for probe in probes:
        expected_frame = seconds_to_frame_number(
            mapping, probe, neighboring_timestamps=_reference_neighbors(rows, probe)
        )
        assert native.seconds_to_frame_number(probe) == expected_frame, probe


@pytest.mark.unit
@pytest.mark.parametrize("origin", [0.0, 1.5])
def test_native_index_matches_constant_rate_fallback(origin: float) -> None:
    mapping = VideoTimelineMapping(
        fps=30.0,
        frame_count=300,
        duration_seconds=10.0,
        requires_persisted_pts=False,
        first_timestamp_seconds=origin,
    )
    native = _native(mapping, [])
    for frame_number in (0, 1, 149, 299, 300):
        assert native.frame_number_to_seconds(frame_number) == frame_number_to_seconds(
            mapping, frame_number, persisted_timestamp=None
        )
    for probe in (
        origin,
        origin + 0.0166667,
        origin + 1 / 60,
        origin + 5.0,
        origin + 10.0,
    ):
        assert native.seconds_to_frame_number(probe) == seconds_to_frame_number(
            mapping, probe, neighboring_timestamps={}
        )


@pytest.mark.unit
def test_native_index_batch_lookups_match_single_lookups() -> None:
    rows, duration = _irregular_rows(seed=3, frame_count=20, origin=0.5)
    mapping = VideoTimelineMapping(
        fps=None,
        frame_count=len(rows),
        duration_seconds=duration,
        requires_persisted_pts=True,
        first_timestamp_seconds=0.5,
    )
    native = _native(mapping, rows)
    frame_numbers = [0, 5, 19, 20]
    assert native.frame_numbers_to_seconds(frame_numbers) == [
        native.frame_number_to_seconds(number) for number in frame_numbers
    ]
    probes = [0.5, 0.61, 0.5 + duration]
    assert native.seconds_to_frame_numbers(probes) == [
        native.seconds_to_frame_number(probe) for probe in probes
    ]


@pytest.mark.unit
@pytest.mark.parametrize(
    ("rows", "message"),
    [
        ([(0, 0.0), (1, 0.0)], "strictly increase"),
        ([(1, 0.1), (0, 0.2)], "strictly increase"),
        ([(0, 0.0), (11, 0.4)], "exceeds frame count"),
        ([(0, math.nan)], "finite and non-negative"),
        ([(0, -0.5)], "finite and non-negative"),
        ([(-1, 0.0)], "must be non-negative"),
    ],
)
def test_native_index_rejects_invalid_persisted_coordinates(
    rows: list[tuple[int, float]], message: str
) -> None:
    mapping = VideoTimelineMapping(
        fps=25.0,
        frame_count=10,
        duration_seconds=0.4,
        requires_persisted_pts=False,
    )
    with pytest.raises(ValueError, match=message):
        _native(mapping, rows)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("frame_number", "timestamp", "message"),
    [
        (-1, None, "must be non-negative"),
        (11, None, "exceeds frame count"),
        (4, None, "no persisted PTS"),
        (None, math.inf, "finite"),
        (None, 9.0, "exceeds video duration"),
    ],
)
def test_native_index_rejects_invalid_lookups(
    frame_number: int | None, timestamp: float | None, message: str
) -> None:
    mapping = VideoTimelineMapping(
        fps=25.0,
        frame_count=10,
        duration_seconds=0.4,
        requires_persisted_pts=True,
    )
    native = _native(mapping, [(0, 0.0), (1, 0.04)])
    with pytest.raises(ValueError, match=message):
        if frame_number is not None:
            native.frame_number_to_seconds(frame_number)
        else:
            assert timestamp is not None
            native.seconds_to_frame_number(timestamp)


@pytest.mark.unit
def test_compose_frame_ancestry_follows_drops_and_repetitions() -> None:
    assert compose_frame_ancestry([6, 4], [[0, 2, 3, 5], [0, 1, 1, 2, 3]]) == [
        0,
        2,
        2,
        3,
        5,
    ]


@pytest.mark.unit
@pytest.mark.parametrize(
    ("counts", "indices", "message"),
    [
        ([], [], "at least one transformation"),
        ([3, 5], [[0, 1, 2], [0]], "does not match the previous output"),
        ([2], [[0, 2]], "missing source frame"),
        ([3], [[1, 0]], "reverses source frame order"),
        ([3], [[0], [0]], "exactly one source frame count"),
    ],
)
def test_compose_frame_ancestry_rejects_invalid_chains(
    counts: list[int], indices: list[list[int]], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        compose_frame_ancestry(counts, indices)


@pytest.mark.unit
def test_timeline_mapping_error_remains_a_value_error() -> None:
    assert issubclass(VideoTimelineMappingError, ValueError)
