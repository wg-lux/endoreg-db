use crate::errors::map_io_error;
use pyo3::prelude::*;
use std::fs::File;
use std::io::Read;
use std::path::PathBuf;

const LX_ENCRYPTED_MAGIC: &[u8; 8] = b"LXENC01\n";

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum EncryptionStatus {
    Encrypted,
    Plaintext,
}

impl EncryptionStatus {
    const fn as_str(self) -> &'static str {
        match self {
            Self::Encrypted => "encrypted",
            Self::Plaintext => "plaintext",
        }
    }
}

fn encryption_status_impl(path: PathBuf) -> Result<EncryptionStatus, std::io::Error> {
    let mut file = File::open(path)?;
    let mut buffer = [0_u8; LX_ENCRYPTED_MAGIC.len()];
    let read_count = file.read(&mut buffer)?;
    if read_count == LX_ENCRYPTED_MAGIC.len() && &buffer == LX_ENCRYPTED_MAGIC {
        return Ok(EncryptionStatus::Encrypted);
    }
    Ok(EncryptionStatus::Plaintext)
}

#[pyfunction]
pub(crate) fn encryption_status(py: Python<'_>, path: PathBuf) -> PyResult<&'static str> {
    py.allow_threads(move || encryption_status_impl(path))
        .map(|status| status.as_str())
        .map_err(map_io_error)
}

#[pyfunction]
pub(crate) fn is_lx_encrypted_file(py: Python<'_>, path: PathBuf) -> PyResult<bool> {
    py.allow_threads(move || encryption_status_impl(path))
        .map(|status| status == EncryptionStatus::Encrypted)
        .map_err(map_io_error)
}
