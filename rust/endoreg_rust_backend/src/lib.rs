use pyo3::exceptions::{PyIOError, PyValueError};
use pyo3::prelude::*;
use sha2::{Digest, Sha256};
use std::fs::File;
use std::io::{BufReader, Read};
use std::path::PathBuf;

const DEFAULT_CHUNK_SIZE: usize = 1024 * 1024;
const MAX_LINES_PER_PAGE: usize = 65;

fn map_io_error(err: std::io::Error) -> PyErr {
    PyIOError::new_err(err.to_string())
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
fn sha256_file_hex(path: PathBuf, chunk_size: usize) -> PyResult<String> {
    if chunk_size == 0 {
        return Err(PyValueError::new_err(
            "chunk_size must be greater than zero",
        ));
    }

    let file = File::open(path).map_err(map_io_error)?;
    let mut reader = BufReader::with_capacity(chunk_size, file);
    let mut hasher = Sha256::new();
    let mut buffer = vec![0_u8; chunk_size];

    loop {
        let read_count = reader.read(&mut buffer).map_err(map_io_error)?;
        if read_count == 0 {
            break;
        }
        hasher.update(&buffer[..read_count]);
    }

    Ok(format!("{:x}", hasher.finalize()))
}

#[pyfunction]
fn render_single_page_pdf(text: &str) -> PyResult<Vec<u8>> {
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

    Ok(payload)
}

#[pyfunction]
fn parse_extracted_frame_numbers(paths: Vec<String>) -> PyResult<Vec<usize>> {
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

#[pymodule]
fn endoreg_rust_backend(_py: Python<'_>, module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(sha256_file_hex, module)?)?;
    module.add_function(wrap_pyfunction!(render_single_page_pdf, module)?)?;
    module.add_function(wrap_pyfunction!(parse_extracted_frame_numbers, module)?)?;
    Ok(())
}
