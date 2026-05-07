use pyo3::prelude::*;

#[pyfunction]
#[pyo3(signature = (values, threshold, min_length=None))]
fn threshold_runs(values: Vec<f64>, threshold: f64, min_length: Option<usize>) -> PyResult<Vec<(usize, usize)>> {
    let min_len = min_length.unwrap_or(1).max(1);
    let mut runs: Vec<(usize, usize)> = Vec::new();
    let mut start: Option<usize> = None;

    for (index, value) in values.iter().enumerate() {
        if *value >= threshold {
            if start.is_none() {
                start = Some(index);
            }
        } else if let Some(run_start) = start {
            let end = index - 1;
            if end - run_start + 1 >= min_len {
                runs.push((run_start, end));
            }
            start = None;
        }
    }

    if let Some(run_start) = start {
        let end = values.len() - 1;
        if end - run_start + 1 >= min_len {
            runs.push((run_start, end));
        }
    }

    Ok(runs)
}

#[pyfunction]
fn smooth_scores(values: Vec<f64>, window: usize) -> PyResult<Vec<f64>> {
    if window <= 1 {
        return Ok(values);
    }
    let radius = window / 2;
    let mut out: Vec<f64> = Vec::with_capacity(values.len());
    for index in 0..values.len() {
        let start = index.saturating_sub(radius);
        let end = (index + radius + 1).min(values.len());
        let mut total = 0.0;
        for value in values[start..end].iter() {
            total += *value;
        }
        out.push(total / ((end - start) as f64));
    }
    Ok(out)
}

#[pyfunction]
fn mask_rle_encode(mask: Vec<u8>) -> PyResult<Vec<usize>> {
    let mut counts: Vec<usize> = Vec::new();
    let mut current: u8 = 0;
    let mut run_len: usize = 0;

    for raw in mask.iter() {
        let value = if *raw == 0 { 0 } else { 1 };
        if value == current {
            run_len += 1;
        } else {
            counts.push(run_len);
            current = value;
            run_len = 1;
        }
    }

    counts.push(run_len);
    Ok(counts)
}

#[pyfunction]
fn mask_rle_decode(counts: Vec<usize>) -> PyResult<Vec<u8>> {
    let mut out: Vec<u8> = Vec::new();
    let mut value: u8 = 0;
    for count in counts.iter() {
        for _ in 0..*count {
            out.push(value);
        }
        value = 1 - value;
    }
    Ok(out)
}

#[pymodule]
fn _native(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(threshold_runs, m)?)?;
    m.add_function(wrap_pyfunction!(smooth_scores, m)?)?;
    m.add_function(wrap_pyfunction!(mask_rle_encode, m)?)?;
    m.add_function(wrap_pyfunction!(mask_rle_decode, m)?)?;
    Ok(())
}
