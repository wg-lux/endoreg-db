"""Shared half-open frame interval merging for media and training selection."""


def merge_half_open_frame_intervals(
    intervals: list[tuple[int, int]],
) -> list[tuple[int, int]]:
    """Sort the caller-owned list and merge overlapping or adjacent ranges."""
    if not intervals:
        return []
    intervals.sort()
    merged: list[tuple[int, int]] = [intervals[0]]
    for start_frame, end_frame in intervals[1:]:
        previous_start, previous_end = merged[-1]
        if start_frame <= previous_end:
            merged[-1] = (previous_start, max(previous_end, end_frame))
        else:
            merged.append((start_frame, end_frame))
    return merged
