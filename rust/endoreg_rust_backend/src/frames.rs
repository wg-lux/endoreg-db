use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use std::path::{Path, PathBuf};

fn parse_extracted_frame_numbers_impl(paths: Vec<String>) -> PyResult<Vec<usize>> {
    let mut frame_numbers = Vec::with_capacity(paths.len());

    for raw_path in paths {
        let path = PathBuf::from(raw_path);
        let stem = path
            .file_stem()
            .and_then(|value| value.to_str())
            .ok_or_else(|| PyValueError::new_err("path is missing a valid file stem"))?;
        let frame_part = stem
            .rsplit('_')
            .next()
            .ok_or_else(|| PyValueError::new_err("path stem is missing an underscore"))?;
        let frame_number = frame_part.parse::<usize>().map_err(|_| {
            PyValueError::new_err(format!("invalid frame number in path: {}", path.display()))
        })?;
        frame_numbers.push(frame_number);
    }

    Ok(frame_numbers)
}

#[pyfunction]
pub(crate) fn parse_extracted_frame_numbers(
    py: Python<'_>,
    paths: Vec<String>,
) -> PyResult<Vec<usize>> {
    py.allow_threads(move || parse_extracted_frame_numbers_impl(paths))
}

fn normalize_relative_path(path: &Path, relative_to: Option<&Path>) -> PyResult<String> {
    if let Some(base_path) = relative_to {
        let relative = path.strip_prefix(base_path).map_err(|_| {
            PyValueError::new_err(format!(
                "path is not relative to base directory: {}",
                path.display()
            ))
        })?;
        return Ok(relative.to_string_lossy().replace('\\', "/"));
    }

    let file_name = path
        .file_name()
        .and_then(|value| value.to_str())
        .ok_or_else(|| PyValueError::new_err("path is missing a valid file name"))?;
    Ok(file_name.to_string())
}

fn build_frame_records_impl(
    paths: Vec<String>,
    relative_to: Option<PathBuf>,
    zero_based: bool,
) -> PyResult<Vec<(usize, String)>> {
    let relative_base_ref = relative_to.as_deref();
    let mut records = Vec::with_capacity(paths.len());

    for raw_path in paths {
        let path = PathBuf::from(raw_path);
        let stem = path
            .file_stem()
            .and_then(|value| value.to_str())
            .ok_or_else(|| PyValueError::new_err("path is missing a valid file stem"))?;
        let frame_part = stem
            .rsplit('_')
            .next()
            .ok_or_else(|| PyValueError::new_err("path stem is missing an underscore"))?;
        let mut frame_number = frame_part.parse::<usize>().map_err(|_| {
            PyValueError::new_err(format!("invalid frame number in path: {}", path.display()))
        })?;
        if zero_based {
            frame_number = frame_number.checked_sub(1).ok_or_else(|| {
                PyValueError::new_err(format!(
                    "frame number cannot be shifted to zero-based index: {}",
                    path.display()
                ))
            })?;
        }
        let relative_path = normalize_relative_path(&path, relative_base_ref)?;
        records.push((frame_number, relative_path));
    }

    Ok(records)
}

#[pyfunction]
#[pyo3(signature = (frame_count, ext="jpg"))]
pub(crate) fn build_expected_frame_records(
    py: Python<'_>,
    frame_count: usize,
    ext: &str,
) -> PyResult<Vec<(usize, String)>> {
    let owned_ext = ext.to_owned();
    py.allow_threads(move || build_expected_frame_records_impl(frame_count, owned_ext))
}

fn build_expected_frame_records_impl(
    frame_count: usize,
    ext: String,
) -> PyResult<Vec<(usize, String)>> {
    if ext.trim().is_empty() {
        return Err(PyValueError::new_err("ext must not be empty"));
    }

    let mut records = Vec::with_capacity(frame_count);
    for frame_number in 0..frame_count {
        records.push((frame_number, format!("frame_{frame_number:07}.{ext}")));
    }
    Ok(records)
}

#[pyfunction]
#[pyo3(signature = (paths, *, relative_to=None, zero_based=false))]
pub(crate) fn build_frame_records(
    py: Python<'_>,
    paths: Vec<String>,
    relative_to: Option<String>,
    zero_based: bool,
) -> PyResult<Vec<(usize, String)>> {
    let relative_base = relative_to.map(PathBuf::from);
    py.allow_threads(move || build_frame_records_impl(paths, relative_base, zero_based))
}
