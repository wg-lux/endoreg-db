use crate::errors::InvalidInput;
use pyo3::prelude::*;
use pyo3_stub_gen::derive::{gen_stub_pyclass, gen_stub_pymethods};

/// Matches `math.isclose(..., rel_tol=0.0, abs_tol=1e-9)` in timeline.py.
const TERMINAL_TOLERANCE_SECONDS: f64 = 1e-9;

fn validated_timestamp(value: f64, field_name: &str) -> Result<f64, InvalidInput> {
    if !value.is_finite() || value < 0.0 {
        return Err(InvalidInput(format!(
            "{field_name} must be finite and non-negative."
        )));
    }
    Ok(value)
}

fn is_terminal(timestamp: f64, end: f64) -> bool {
    (timestamp - end).abs() <= TERMINAL_TOLERANCE_SECONDS
}

/// Timeline metadata; mirrors `VideoTimelineMapping` validation.
#[derive(Debug, Clone, PartialEq)]
struct TimelineRules {
    fps: Option<f64>,
    frame_count: Option<i64>,
    duration_seconds: Option<f64>,
    requires_persisted_pts: bool,
    first_timestamp_seconds: f64,
}

impl TimelineRules {
    fn new(
        fps: Option<f64>,
        frame_count: Option<i64>,
        duration_seconds: Option<f64>,
        requires_persisted_pts: bool,
        first_timestamp_seconds: f64,
    ) -> Result<Self, InvalidInput> {
        validated_timestamp(first_timestamp_seconds, "Timeline origin")?;
        if let Some(rate) = fps {
            if !rate.is_finite() || rate <= 0.0 {
                return Err(InvalidInput(
                    "FPS must be set, finite, and greater than zero.".to_owned(),
                ));
            }
        }
        if matches!(frame_count, Some(count) if count < 0) {
            return Err(InvalidInput("Frame count must be non-negative.".to_owned()));
        }
        if let Some(duration) = duration_seconds {
            if !duration.is_finite() || duration < 0.0 {
                return Err(InvalidInput(
                    "Duration must be finite and non-negative.".to_owned(),
                ));
            }
        }
        Ok(Self {
            fps,
            frame_count,
            duration_seconds,
            requires_persisted_pts,
            first_timestamp_seconds,
        })
    }

    fn end_timestamp_seconds(&self) -> Option<f64> {
        self.duration_seconds
            .map(|duration| self.first_timestamp_seconds + duration)
    }

    fn validate_frame_number(&self, frame_number: i64) -> Result<(), InvalidInput> {
        if frame_number < 0 {
            return Err(InvalidInput(
                "Frame number must be non-negative.".to_owned(),
            ));
        }
        if let Some(count) = self.frame_count {
            if frame_number > count {
                return Err(InvalidInput(format!(
                    "Frame number {frame_number} exceeds frame count {count}."
                )));
            }
        }
        Ok(())
    }

    fn require_fps(&self) -> Result<f64, InvalidInput> {
        self.fps
            .ok_or_else(|| InvalidInput("FPS must be set and greater than zero.".to_owned()))
    }
}

/// Persisted boundaries ordered by frame number with strictly increasing time.
#[derive(Debug, Clone, PartialEq)]
struct TimelineCore {
    rules: TimelineRules,
    frame_numbers: Vec<i64>,
    timestamps: Vec<f64>,
}

impl TimelineCore {
    fn new(
        rules: TimelineRules,
        frame_numbers: Vec<i64>,
        timestamps: Vec<f64>,
    ) -> Result<Self, InvalidInput> {
        if frame_numbers.len() != timestamps.len() {
            return Err(InvalidInput(
                "Persisted PTS frame numbers and timestamps must have equal length.".to_owned(),
            ));
        }
        for (position, (&frame_number, &timestamp)) in
            frame_numbers.iter().zip(timestamps.iter()).enumerate()
        {
            if frame_number < 0 {
                return Err(InvalidInput(
                    "Persisted PTS frame numbers must be non-negative.".to_owned(),
                ));
            }
            if let Some(count) = rules.frame_count {
                if frame_number > count {
                    return Err(InvalidInput(format!(
                        "Persisted PTS frame {frame_number} exceeds frame count {count}."
                    )));
                }
            }
            validated_timestamp(
                timestamp,
                &format!("Persisted PTS for frame {frame_number}"),
            )?;
            if position > 0
                && (frame_number <= frame_numbers[position - 1]
                    || timestamp <= timestamps[position - 1])
            {
                return Err(InvalidInput(format!(
                    "Persisted PTS must strictly increase with frame number at frame {frame_number}."
                )));
            }
        }
        Ok(Self {
            rules,
            frame_numbers,
            timestamps,
        })
    }

    fn persisted_timestamp(&self, frame_number: i64) -> Option<f64> {
        self.frame_numbers
            .binary_search(&frame_number)
            .ok()
            .map(|position| self.timestamps[position])
    }

    fn frame_number_to_seconds(&self, frame_number: i64) -> Result<f64, InvalidInput> {
        let rules = &self.rules;
        rules.validate_frame_number(frame_number)?;
        if let Some(timestamp) = self.persisted_timestamp(frame_number) {
            return Ok(timestamp);
        }
        if let (Some(count), Some(duration)) = (rules.frame_count, rules.duration_seconds) {
            if frame_number == count && duration > 0.0 {
                return Ok(rules.first_timestamp_seconds + duration);
            }
        }
        if rules.requires_persisted_pts {
            return Err(InvalidInput(format!(
                "Frame {frame_number} has no persisted PTS for a VFR timeline."
            )));
        }
        let fps = rules.require_fps()?;
        Ok(rules.first_timestamp_seconds + frame_number as f64 / fps)
    }

    /// Nearest persisted or terminal boundary; equidistant boundaries select
    /// the lower frame number, matching `seconds_to_frame_number`.
    fn seconds_to_frame_number(&self, timestamp_seconds: f64) -> Result<i64, InvalidInput> {
        let rules = &self.rules;
        let timestamp = validated_timestamp(timestamp_seconds, "Timestamp")?;
        let end = rules.end_timestamp_seconds();
        if let Some(end) = end {
            if timestamp > end && !is_terminal(timestamp, end) {
                return Err(InvalidInput(format!(
                    "Timestamp {timestamp}s exceeds video duration {end}s."
                )));
            }
        }

        if !self.timestamps.is_empty() {
            let at_or_before = self.timestamps.partition_point(|value| *value <= timestamp);
            let mut candidates: Vec<(i64, f64)> = Vec::with_capacity(3);
            if at_or_before > 0 {
                let position = at_or_before - 1;
                candidates.push((self.frame_numbers[position], self.timestamps[position]));
            }
            let exact_match = at_or_before > 0 && self.timestamps[at_or_before - 1] == timestamp;
            if !exact_match && at_or_before < self.timestamps.len() {
                candidates.push((
                    self.frame_numbers[at_or_before],
                    self.timestamps[at_or_before],
                ));
            }
            if let (Some(count), Some(end)) = (rules.frame_count, end) {
                candidates.retain(|(frame_number, _)| *frame_number != count);
                candidates.push((count, end));
            }
            let nearest = candidates
                .iter()
                .min_by(|left, right| {
                    let left_distance = (left.1 - timestamp).abs();
                    let right_distance = (right.1 - timestamp).abs();
                    left_distance
                        .total_cmp(&right_distance)
                        .then(left.0.cmp(&right.0))
                })
                .map(|(frame_number, _)| *frame_number);
            if let Some(frame_number) = nearest {
                return Ok(frame_number);
            }
        }

        if let (Some(count), Some(end)) = (rules.frame_count, end) {
            if is_terminal(timestamp, end) {
                return Ok(count);
            }
        }
        if rules.requires_persisted_pts {
            return Err(InvalidInput(
                "VFR timestamp conversion requires persisted frame PTS.".to_owned(),
            ));
        }
        let fps = rules.require_fps()?;
        let rounded = ((timestamp - rules.first_timestamp_seconds) * fps + 0.5).floor();
        let frame_number = if rounded > 0.0 { rounded as i64 } else { 0 };
        Ok(match rules.frame_count {
            Some(count) => frame_number.min(count),
            None => frame_number,
        })
    }
}

/// Generation-bound frame/time resolver loaded once per video and request.
///
/// It never mutates coordinates; it only answers boundary lookups using the
/// same rules as `endoreg_db.services.video_files.timeline`.
#[gen_stub_pyclass]
#[pyclass(frozen)]
pub(crate) struct TimelineIndex {
    core: TimelineCore,
    content_hash: Option<String>,
}

#[gen_stub_pymethods]
#[pymethods]
impl TimelineIndex {
    #[new]
    #[pyo3(signature = (
        frame_numbers,
        timestamps,
        *,
        fps,
        frame_count,
        duration_seconds,
        requires_persisted_pts,
        first_timestamp_seconds,
        content_hash,
    ))]
    #[allow(clippy::too_many_arguments)]
    pub(crate) fn new(
        frame_numbers: Vec<i64>,
        timestamps: Vec<f64>,
        fps: Option<f64>,
        frame_count: Option<i64>,
        duration_seconds: Option<f64>,
        requires_persisted_pts: bool,
        first_timestamp_seconds: f64,
        content_hash: Option<String>,
    ) -> PyResult<Self> {
        let rules = TimelineRules::new(
            fps,
            frame_count,
            duration_seconds,
            requires_persisted_pts,
            first_timestamp_seconds,
        )?;
        let core = TimelineCore::new(rules, frame_numbers, timestamps)?;
        Ok(Self { core, content_hash })
    }

    #[getter]
    pub(crate) fn content_hash(&self) -> Option<String> {
        self.content_hash.clone()
    }

    #[getter]
    pub(crate) fn persisted_count(&self) -> usize {
        self.core.frame_numbers.len()
    }

    pub(crate) fn frame_number_to_seconds(&self, frame_number: i64) -> PyResult<f64> {
        Ok(self.core.frame_number_to_seconds(frame_number)?)
    }

    pub(crate) fn seconds_to_frame_number(&self, timestamp_seconds: f64) -> PyResult<i64> {
        Ok(self.core.seconds_to_frame_number(timestamp_seconds)?)
    }

    pub(crate) fn frame_numbers_to_seconds(&self, frame_numbers: Vec<i64>) -> PyResult<Vec<f64>> {
        Ok(frame_numbers
            .into_iter()
            .map(|frame_number| self.core.frame_number_to_seconds(frame_number))
            .collect::<Result<Vec<_>, _>>()?)
    }

    pub(crate) fn seconds_to_frame_numbers(&self, timestamps: Vec<f64>) -> PyResult<Vec<i64>> {
        Ok(timestamps
            .into_iter()
            .map(|timestamp| self.core.seconds_to_frame_number(timestamp))
            .collect::<Result<Vec<_>, _>>()?)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn variable_rate_core() -> TimelineCore {
        let rules = TimelineRules::new(Some(25.0), Some(4), Some(0.5), true, 2.0).unwrap();
        TimelineCore::new(rules, vec![0, 1, 2, 3], vec![2.0, 2.04, 2.2, 2.25]).unwrap()
    }

    #[test]
    fn persisted_and_terminal_boundaries_resolve() {
        let core = variable_rate_core();
        assert_eq!(core.frame_number_to_seconds(2).unwrap(), 2.2);
        assert_eq!(core.frame_number_to_seconds(4).unwrap(), 2.5);
        assert_eq!(core.seconds_to_frame_number(2.5).unwrap(), 4);
        assert_eq!(core.seconds_to_frame_number(2.21).unwrap(), 2);
    }

    #[test]
    fn equidistant_boundaries_select_lower_frame() {
        let rules = TimelineRules::new(None, Some(2), Some(2.0), true, 0.0).unwrap();
        let core = TimelineCore::new(rules, vec![0, 1], vec![0.0, 1.0]).unwrap();
        assert_eq!(core.seconds_to_frame_number(0.5).unwrap(), 0);
        assert_eq!(core.seconds_to_frame_number(1.5).unwrap(), 1);
    }

    #[test]
    fn constant_rate_fallback_rounds_half_up() {
        let rules = TimelineRules::new(Some(10.0), Some(100), Some(10.0), false, 0.0).unwrap();
        let core = TimelineCore::new(rules, vec![], vec![]).unwrap();
        assert_eq!(core.seconds_to_frame_number(0.25).unwrap(), 3);
        assert_eq!(core.frame_number_to_seconds(5).unwrap(), 0.5);
        assert_eq!(core.seconds_to_frame_number(10.0).unwrap(), 100);
    }

    #[test]
    fn variable_rate_without_persisted_pts_fails() {
        let rules = TimelineRules::new(Some(25.0), Some(10), None, true, 0.0).unwrap();
        let core = TimelineCore::new(rules, vec![], vec![]).unwrap();
        assert!(core.frame_number_to_seconds(3).is_err());
        assert!(core.seconds_to_frame_number(0.1).is_err());
    }

    #[test]
    fn invalid_persisted_rows_fail_construction() {
        let rules = TimelineRules::new(Some(25.0), Some(3), None, false, 0.0).unwrap();
        assert!(TimelineCore::new(rules.clone(), vec![0, 1], vec![0.1, 0.1]).is_err());
        assert!(TimelineCore::new(rules.clone(), vec![0, 4], vec![0.0, 0.1]).is_err());
        assert!(TimelineCore::new(rules.clone(), vec![0], vec![f64::NAN]).is_err());
        assert!(TimelineCore::new(rules, vec![0], vec![]).is_err());
    }

    #[test]
    fn out_of_range_inputs_fail() {
        let core = variable_rate_core();
        assert!(core.frame_number_to_seconds(-1).is_err());
        assert!(core.frame_number_to_seconds(5).is_err());
        assert!(core.seconds_to_frame_number(-0.1).is_err());
        assert!(core.seconds_to_frame_number(3.0).is_err());
    }
}
