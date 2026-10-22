use crate::errors::InvalidInput;
use pyo3::prelude::*;

/// Compose decoder ancestry from the recording to the final output.
///
/// `source_frame_counts[step]` is the decoded source length of a transformation
/// and `source_frame_indices[step][output]` its observed source frame. Steps are
/// ordered from the recording to the final generation. The result maps each
/// final output frame index to its original-recording frame index. Timestamp
/// proximity is never used.
fn compose_frame_ancestry_impl(
    source_frame_counts: &[usize],
    source_frame_indices: &[Vec<usize>],
) -> Result<Vec<usize>, InvalidInput> {
    if source_frame_indices.is_empty() {
        return Err(InvalidInput(
            "Frame ancestry requires at least one transformation.".to_owned(),
        ));
    }
    if source_frame_counts.len() != source_frame_indices.len() {
        return Err(InvalidInput(
            "Every transformation requires exactly one source frame count.".to_owned(),
        ));
    }
    for (step, indices) in source_frame_indices.iter().enumerate() {
        if indices.is_empty() {
            return Err(InvalidInput(format!(
                "Transformation {step} has no output frames."
            )));
        }
        if step > 0 && source_frame_counts[step] != source_frame_indices[step - 1].len() {
            return Err(InvalidInput(format!(
                "Transformation {step} source does not match the previous output."
            )));
        }
        let source_count = source_frame_counts[step];
        for (output, &source) in indices.iter().enumerate() {
            if source >= source_count {
                return Err(InvalidInput(format!(
                    "Transformation {step} output {output} references missing source frame {source}."
                )));
            }
            if output > 0 && source < indices[output - 1] {
                return Err(InvalidInput(format!(
                    "Transformation {step} reverses source frame order at output {output}."
                )));
            }
        }
    }

    let (last, earlier) = source_frame_indices
        .split_last()
        .expect("transformations are non-empty");
    let mut ancestry = last.clone();
    for indices in earlier.iter().rev() {
        for frame in ancestry.iter_mut() {
            *frame = indices[*frame];
        }
    }
    Ok(ancestry)
}

#[pyfunction]
pub(crate) fn compose_frame_ancestry(
    py: Python<'_>,
    source_frame_counts: Vec<usize>,
    source_frame_indices: Vec<Vec<usize>>,
) -> PyResult<Vec<usize>> {
    py.allow_threads(move || {
        compose_frame_ancestry_impl(&source_frame_counts, &source_frame_indices)
    })
    .map_err(Into::into)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn composes_drops_and_repetitions_across_steps() {
        // Recording (6 frames) -> drop frames 1 and 4 -> repeat the second frame.
        let ancestry =
            compose_frame_ancestry_impl(&[6, 4], &[vec![0, 2, 3, 5], vec![0, 1, 1, 2, 3]]).unwrap();
        assert_eq!(ancestry, vec![0, 2, 2, 3, 5]);
    }

    #[test]
    fn single_identity_step_is_unchanged() {
        assert_eq!(
            compose_frame_ancestry_impl(&[3], &[vec![0, 1, 2]]).unwrap(),
            vec![0, 1, 2]
        );
    }

    #[test]
    fn rejects_disconnected_out_of_range_and_reversed_chains() {
        assert!(compose_frame_ancestry_impl(&[], &[]).is_err());
        assert!(compose_frame_ancestry_impl(&[3, 5], &[vec![0, 1, 2], vec![0]]).is_err());
        assert!(compose_frame_ancestry_impl(&[2], &[vec![0, 2]]).is_err());
        assert!(compose_frame_ancestry_impl(&[3], &[vec![1, 0]]).is_err());
        assert!(compose_frame_ancestry_impl(&[3], &[vec![]]).is_err());
        assert!(compose_frame_ancestry_impl(&[3], &[vec![0], vec![0]]).is_err());
    }
}
