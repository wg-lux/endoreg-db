use crate::state_token::{state_tokens, Vocabulary};
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;

state_tokens! {
    pub(crate) enum SegmentAnnotationStatus {
        NotStarted => "not_started",
        CleanupRequired => "cleanup_required",
        CleanupQueued => "cleanup_queued",
        CleanupRunning => "cleanup_running",
        CleanupFailed => "cleanup_failed",
        Validated => "validated",
    }
}

state_tokens! {
    enum FrameAnnotationStatus {
        NotStarted => "not_started",
        FramesUnavailable => "frames_unavailable",
        PredictionPending => "prediction_pending",
        PredictionReady => "prediction_ready",
        AnnotationReady => "annotation_ready",
        AnnotationComplete => "annotation_complete",
    }
}

state_tokens! {
    enum FrameTaskMode {
        Random => "random",
        Filtered => "filtered",
    }
}

state_tokens! {
    enum FrameSamplingStrategy {
        Balanced => "balanced",
        Segments => "segments",
        Annotations => "annotations",
        None => "none",
    }
}

pub(crate) fn vocabularies() -> Vec<Vocabulary> {
    vec![
        (
            "segment_annotation_status",
            SegmentAnnotationStatus::tokens(),
        ),
        ("frame_annotation_status", FrameAnnotationStatus::tokens()),
        ("frame_task_mode", FrameTaskMode::tokens()),
        ("frame_sampling_strategy", FrameSamplingStrategy::tokens()),
    ]
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

fn normalize_task_mode(value: &str) -> Option<FrameTaskMode> {
    FrameTaskMode::parse(&value.trim().to_ascii_lowercase())
}

fn normalize_sampling_strategy(value: &str) -> Option<FrameSamplingStrategy> {
    FrameSamplingStrategy::parse(&value.trim().to_ascii_lowercase())
}

#[pyfunction]
#[pyo3(signature = (created, validated, outside_removed, history))]
pub(crate) fn segment_status_with_history(
    created: bool,
    validated: bool,
    outside_removed: bool,
    history: Option<&str>,
) -> PyResult<&'static str> {
    use crate::state_token::StateToken;
    use crate::status_transition::ProcessingHistory as H;
    let history = history
        .map(|token| {
            H::parse_token(token)
                .ok_or_else(|| PyValueError::new_err("Unknown segment rebuild history status"))
        })
        .transpose()?;
    Ok(match history {
        Some(H::Pending) => SegmentAnnotationStatus::CleanupQueued,
        Some(H::Running) => SegmentAnnotationStatus::CleanupRunning,
        Some(H::Failure) => SegmentAnnotationStatus::CleanupFailed,
        None | Some(H::Success | H::Cancelled) => {
            resolve_segment_annotation_status(created, validated, outside_removed)
        }
    }
    .as_str())
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
pub(crate) fn normalize_frame_task_mode_token(value: &str) -> PyResult<&'static str> {
    normalize_task_mode(value)
        .map(FrameTaskMode::as_str)
        .ok_or_else(|| PyValueError::new_err(format!("unsupported frame task mode: {value}")))
}

#[pyfunction]
pub(crate) fn normalize_frame_sampling_strategy_token(value: &str) -> PyResult<&'static str> {
    normalize_sampling_strategy(value)
        .map(FrameSamplingStrategy::as_str)
        .ok_or_else(|| {
            PyValueError::new_err(format!("unsupported frame sampling strategy: {value}"))
        })
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
        assert_eq!(
            normalize_task_mode(" Filtered "),
            Some(FrameTaskMode::Filtered)
        );
        assert_eq!(normalize_task_mode("random"), Some(FrameTaskMode::Random));
        for token in ["", "unexpected", "filter"] {
            assert_eq!(normalize_task_mode(token), None);
        }
    }

    #[test]
    fn normalizes_frame_sampling_tokens() {
        for strategy in FrameSamplingStrategy::ALL {
            assert_eq!(
                normalize_sampling_strategy(&format!(" {} ", strategy.as_str().to_uppercase())),
                Some(*strategy)
            );
        }
        for token in ["", "unexpected", "segment"] {
            assert_eq!(normalize_sampling_strategy(token), None);
        }
    }
}
