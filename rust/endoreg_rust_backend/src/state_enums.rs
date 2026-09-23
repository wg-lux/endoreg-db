use pyo3::prelude::*;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
#[allow(dead_code)]
enum SegmentAnnotationStatus {
    NotStarted,
    CleanupRequired,
    CleanupQueued,
    CleanupRunning,
    CleanupFailed,
    Validated,
}

impl SegmentAnnotationStatus {
    const fn as_str(self) -> &'static str {
        match self {
            Self::NotStarted => "not_started",
            Self::CleanupRequired => "cleanup_required",
            Self::CleanupQueued => "cleanup_queued",
            Self::CleanupRunning => "cleanup_running",
            Self::CleanupFailed => "cleanup_failed",
            Self::Validated => "validated",
        }
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum FrameAnnotationStatus {
    NotStarted,
    FramesUnavailable,
    PredictionPending,
    PredictionReady,
    AnnotationReady,
    AnnotationComplete,
}

impl FrameAnnotationStatus {
    const fn as_str(self) -> &'static str {
        match self {
            Self::NotStarted => "not_started",
            Self::FramesUnavailable => "frames_unavailable",
            Self::PredictionPending => "prediction_pending",
            Self::PredictionReady => "prediction_ready",
            Self::AnnotationReady => "annotation_ready",
            Self::AnnotationComplete => "annotation_complete",
        }
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum FrameTaskMode {
    Random,
    Filtered,
}

impl FrameTaskMode {
    const fn as_str(self) -> &'static str {
        match self {
            Self::Random => "random",
            Self::Filtered => "filtered",
        }
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum FrameSamplingStrategy {
    Balanced,
    Segments,
    Annotations,
    None,
}

impl FrameSamplingStrategy {
    const fn as_str(self) -> &'static str {
        match self {
            Self::Balanced => "balanced",
            Self::Segments => "segments",
            Self::Annotations => "annotations",
            Self::None => "none",
        }
    }
}

fn resolve_segment_annotation_status(
    segment_annotations_created: bool,
    segment_annotations_validated: bool,
    outside_segments_removed: bool,
) -> SegmentAnnotationStatus {
    if segment_annotations_validated && outside_segments_removed {
        return SegmentAnnotationStatus::Validated;
    }
    if segment_annotations_validated || segment_annotations_created {
        return SegmentAnnotationStatus::CleanupRequired;
    }
    SegmentAnnotationStatus::NotStarted
}

fn resolve_frame_annotation_status(
    has_state: bool,
    frames_extracted: bool,
    initial_prediction_completed: bool,
    lvs_created: bool,
    frame_annotations_generated: bool,
) -> FrameAnnotationStatus {
    if !has_state {
        return FrameAnnotationStatus::NotStarted;
    }
    if !frames_extracted {
        return FrameAnnotationStatus::FramesUnavailable;
    }
    if !initial_prediction_completed {
        return FrameAnnotationStatus::PredictionPending;
    }
    if !lvs_created {
        return FrameAnnotationStatus::PredictionReady;
    }
    if frame_annotations_generated {
        return FrameAnnotationStatus::AnnotationComplete;
    }
    FrameAnnotationStatus::AnnotationReady
}

fn normalize_task_mode(value: &str) -> FrameTaskMode {
    if value.trim().to_ascii_lowercase() == FrameTaskMode::Filtered.as_str() {
        FrameTaskMode::Filtered
    } else {
        FrameTaskMode::Random
    }
}

fn normalize_sampling_strategy(value: &str) -> FrameSamplingStrategy {
    match value.trim().to_ascii_lowercase().as_str() {
        "segments" => FrameSamplingStrategy::Segments,
        "annotations" => FrameSamplingStrategy::Annotations,
        "none" => FrameSamplingStrategy::None,
        "balanced" => FrameSamplingStrategy::Balanced,
        _ => FrameSamplingStrategy::Balanced,
    }
}

#[pyfunction]
pub(crate) fn derive_segment_annotation_status(
    segment_annotations_created: bool,
    segment_annotations_validated: bool,
    outside_segments_removed: bool,
) -> &'static str {
    resolve_segment_annotation_status(
        segment_annotations_created,
        segment_annotations_validated,
        outside_segments_removed,
    )
    .as_str()
}

#[pyfunction]
pub(crate) fn derive_frame_annotation_status(
    has_state: bool,
    frames_extracted: bool,
    initial_prediction_completed: bool,
    lvs_created: bool,
    frame_annotations_generated: bool,
) -> &'static str {
    resolve_frame_annotation_status(
        has_state,
        frames_extracted,
        initial_prediction_completed,
        lvs_created,
        frame_annotations_generated,
    )
    .as_str()
}

#[pyfunction]
pub(crate) fn normalize_frame_task_mode_token(value: &str) -> &'static str {
    normalize_task_mode(value).as_str()
}

#[pyfunction]
pub(crate) fn normalize_frame_sampling_strategy_token(value: &str) -> &'static str {
    normalize_sampling_strategy(value).as_str()
}

#[cfg(test)]
mod tests {
    use super::{
        normalize_sampling_strategy, normalize_task_mode, resolve_frame_annotation_status,
        resolve_segment_annotation_status, FrameAnnotationStatus, FrameSamplingStrategy,
        FrameTaskMode, SegmentAnnotationStatus,
    };

    #[test]
    fn resolves_segment_annotation_status_from_flags() {
        let cases = [
            ((false, false, false), SegmentAnnotationStatus::NotStarted),
            (
                (true, false, false),
                SegmentAnnotationStatus::CleanupRequired,
            ),
            (
                (false, true, false),
                SegmentAnnotationStatus::CleanupRequired,
            ),
            ((true, true, true), SegmentAnnotationStatus::Validated),
        ];

        for ((created, validated, removed), expected) in cases {
            assert_eq!(
                resolve_segment_annotation_status(created, validated, removed),
                expected
            );
        }
    }

    #[test]
    fn resolves_frame_annotation_status_from_flags() {
        let cases = [
            (
                (false, false, false, false, false),
                FrameAnnotationStatus::NotStarted,
            ),
            (
                (true, false, false, false, false),
                FrameAnnotationStatus::FramesUnavailable,
            ),
            (
                (true, true, false, false, false),
                FrameAnnotationStatus::PredictionPending,
            ),
            (
                (true, true, true, false, false),
                FrameAnnotationStatus::PredictionReady,
            ),
            (
                (true, true, true, true, false),
                FrameAnnotationStatus::AnnotationReady,
            ),
            (
                (true, true, true, true, true),
                FrameAnnotationStatus::AnnotationComplete,
            ),
        ];

        for ((has_state, frames, prediction, lvs, generated), expected) in cases {
            assert_eq!(
                resolve_frame_annotation_status(has_state, frames, prediction, lvs, generated),
                expected
            );
        }
    }

    #[test]
    fn normalizes_frame_task_tokens() {
        assert_eq!(normalize_task_mode(" filtered "), FrameTaskMode::Filtered);
        assert_eq!(normalize_task_mode("unexpected"), FrameTaskMode::Random);
    }

    #[test]
    fn normalizes_frame_sampling_tokens() {
        assert_eq!(
            normalize_sampling_strategy("segments"),
            FrameSamplingStrategy::Segments
        );
        assert_eq!(
            normalize_sampling_strategy("annotations"),
            FrameSamplingStrategy::Annotations
        );
        assert_eq!(
            normalize_sampling_strategy("none"),
            FrameSamplingStrategy::None
        );
        assert_eq!(
            normalize_sampling_strategy("unexpected"),
            FrameSamplingStrategy::Balanced
        );
    }
}
