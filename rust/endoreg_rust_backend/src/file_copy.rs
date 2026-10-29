use crate::errors::map_io_error;
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use std::fs::File;
use std::io::{BufReader, BufWriter, Write};
use std::os::fd::{BorrowedFd, FromRawFd, IntoRawFd};
use std::path::PathBuf;

fn copy_file_descriptor_to_path_impl(
    source_fd: i32,
    target_path: PathBuf,
    chunk_size: usize,
) -> Result<u64, std::io::Error> {
    let borrowed_fd = unsafe { BorrowedFd::borrow_raw(source_fd) };
    let owned_fd = borrowed_fd.try_clone_to_owned()?;
    let source = unsafe { File::from_raw_fd(owned_fd.into_raw_fd()) };
    let target = File::create(target_path)?;

    let mut reader = BufReader::with_capacity(chunk_size, source);
    let mut writer = BufWriter::with_capacity(chunk_size, target);
    let copied = std::io::copy(&mut reader, &mut writer)?;
    writer.flush()?;
    writer.get_ref().sync_all()?;
    Ok(copied)
}

#[pyfunction]
#[pyo3(signature = (source_fd, target_path, chunk_size=1024 * 1024))]
pub(crate) fn copy_file_descriptor_to_path(
    py: Python<'_>,
    source_fd: i32,
    target_path: PathBuf,
    chunk_size: usize,
) -> PyResult<u64> {
    if source_fd < 0 {
        return Err(PyValueError::new_err("source_fd must be non-negative"));
    }
    if chunk_size == 0 {
        return Err(PyValueError::new_err(
            "chunk_size must be greater than zero",
        ));
    }

    py.allow_threads(move || copy_file_descriptor_to_path_impl(source_fd, target_path, chunk_size))
        .map_err(map_io_error)
}
