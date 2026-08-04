use pyo3::exceptions::{PyIOError, PyValueError};
use pyo3::prelude::*;
use sha2::{Digest, Sha256};
use std::fs::File;
use std::io::{BufReader, Read};
use std::path::{Path, PathBuf};

const DEFAULT_CHUNK_SIZE: usize = 1024 * 1024;
const MAX_LINES_PER_PAGE: usize = 65;

fn map_io_error(err: std::io::Error) -> PyErr {
    PyIOError::new_err(err.to_string())
}

fn sha256_file_hex_impl(path: PathBuf, chunk_size: usize) -> Result<String, std::io::Error> {
    let file = File::open(path)?;
    let mut reader = BufReader::with_capacity(chunk_size, file);
    let mut hasher = Sha256::new();
    let mut buffer = vec![0_u8; chunk_size];

    loop {
        let read_count = reader.read(&mut buffer)?;
        if read_count == 0 {
            break;
        }
        hasher.update(&buffer[..read_count]);
    }

    Ok(format!("{:x}", hasher.finalize()))
}

fn escape_pdf_text(value: &str) -> String {
    value
        .replace('\\', "\\\\")
        .replace('(', "\\(")
        .replace(')', "\\)")
        .replace('\r', " ")
        .replace('\n', " ")
}

fn latin1_safe_line(value: &str) -> String {
    value
        .chars()
        .map(|ch| if (ch as u32) <= 0xFF { ch } else { '?' })
        .collect()
}

#[pyfunction]
#[pyo3(signature = (path, chunk_size=DEFAULT_CHUNK_SIZE))]
fn sha256_file_hex(py: Python<'_>, path: PathBuf, chunk_size: usize) -> PyResult<String> {
    if chunk_size == 0 {
        return Err(PyValueError::new_err(
            "chunk_size must be greater than zero",
        ));
    }

    py.allow_threads(move || sha256_file_hex_impl(path, chunk_size))
        .map_err(map_io_error)
}

fn render_single_page_pdf_impl(text: &str) -> Vec<u8> {
    let normalized_text = text.replace("\r\n", "\n").replace('\r', "\n");
    let normalized_lines: Vec<&str> = normalized_text.split('\n').collect();

    let lines: Vec<String> = if normalized_lines.is_empty() {
        vec![String::new()]
    } else {
        normalized_lines
            .into_iter()
            .take(MAX_LINES_PER_PAGE)
            .map(latin1_safe_line)
            .collect()
    };

    let mut commands = vec![
        "BT".to_string(),
        "/F1 10 Tf".to_string(),
        "36 806 Td".to_string(),
    ];
    for (idx, line) in lines.iter().enumerate() {
        commands.push(format!("({}) Tj", escape_pdf_text(line)));
        if idx + 1 < lines.len() {
            commands.push("0 -12 Td".to_string());
        }
    }
    commands.push("ET".to_string());
    let stream = commands.join("\n").into_bytes();

    let objects = vec![
        b"<< /Type /Catalog /Pages 2 0 R >>".to_vec(),
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>".to_vec(),
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>".to_vec(),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>".to_vec(),
        [
            format!("<< /Length {} >>\nstream\n", stream.len()).into_bytes(),
            stream,
            b"\nendstream".to_vec(),
        ]
        .concat(),
    ];

    let mut payload = b"%PDF-1.4\n".to_vec();
    let mut offsets = vec![0_usize];
    for (obj_index, obj_payload) in objects.iter().enumerate() {
        offsets.push(payload.len());
        payload.extend_from_slice(format!("{} 0 obj\n", obj_index + 1).as_bytes());
        payload.extend_from_slice(obj_payload);
        payload.extend_from_slice(b"\nendobj\n");
    }

    let startxref = payload.len();
    payload.extend_from_slice(format!("xref\n0 {}\n", objects.len() + 1).as_bytes());
    payload.extend_from_slice(b"0000000000 65535 f \n");
    for offset in offsets.iter().skip(1) {
        payload.extend_from_slice(format!("{offset:010} 00000 n \n").as_bytes());
    }
    payload.extend_from_slice(
        format!(
            "trailer\n<< /Size {} /Root 1 0 R >>\nstartxref\n{}\n%%EOF\n",
            objects.len() + 1,
            startxref
        )
        .as_bytes(),
    );

    payload
}

#[pyfunction]
fn render_single_page_pdf(py: Python<'_>, text: &str) -> PyResult<Vec<u8>> {
    let owned_text = text.to_owned();
    Ok(py.allow_threads(move || render_single_page_pdf_impl(&owned_text)))
}

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
fn parse_extracted_frame_numbers(py: Python<'_>, paths: Vec<String>) -> PyResult<Vec<usize>> {
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
fn build_expected_frame_records(
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
        records.push((
            frame_number,
            format!("frame_{frame_number:07}.{ext}"),
        ));
    }
    Ok(records)
}

#[pyfunction]
#[pyo3(signature = (paths, *, relative_to=None, zero_based=false))]
fn build_frame_records(
    py: Python<'_>,
    paths: Vec<String>,
    relative_to: Option<String>,
    zero_based: bool,
) -> PyResult<Vec<(usize, String)>> {
    let relative_base = relative_to.map(PathBuf::from);
    py.allow_threads(move || build_frame_records_impl(paths, relative_base, zero_based))
}

#[pymodule]
fn endoreg_rust_backend(_py: Python<'_>, module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(sha256_file_hex, module)?)?;
    module.add_function(wrap_pyfunction!(render_single_page_pdf, module)?)?;
    module.add_function(wrap_pyfunction!(parse_extracted_frame_numbers, module)?)?;
    module.add_function(wrap_pyfunction!(build_frame_records, module)?)?;
    module.add_function(wrap_pyfunction!(build_expected_frame_records, module)?)?;
    Ok(())
}
