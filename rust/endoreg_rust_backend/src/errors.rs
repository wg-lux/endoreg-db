use pyo3::exceptions::PyIOError;
use pyo3::PyErr;

pub(crate) fn map_io_error(err: std::io::Error) -> PyErr {
    PyIOError::new_err(err.to_string())
}
