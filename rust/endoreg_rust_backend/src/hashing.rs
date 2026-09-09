use crate::errors::map_io_error;
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use sha2::{Digest, Sha256};
use std::fs::File;
use std::io::{BufReader, Read};
use std::path::PathBuf;

const DEFAULT_CHUNK_SIZE: usize = 1024 * 1024;

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

#[pyfunction]
#[pyo3(signature = (path, chunk_size=DEFAULT_CHUNK_SIZE))]
pub(crate) fn sha256_file_hex(
    py: Python<'_>,
    path: PathBuf,
    chunk_size: usize,
) -> PyResult<String> {
    if chunk_size == 0 {
        return Err(PyValueError::new_err(
            "chunk_size must be greater than zero",
        ));
    }

    py.allow_threads(move || sha256_file_hex_impl(path, chunk_size))
        .map_err(map_io_error)
}
