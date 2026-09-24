use pyo3::exceptions::PyValueError;
use pyo3::PyErr;

/// Validation failures remain native until the Python binding returns.
#[derive(Debug)]
pub(crate) struct InvalidInput(pub(crate) String);

impl From<InvalidInput> for PyErr {
    fn from(error: InvalidInput) -> Self {
        PyValueError::new_err(error.0)
    }
}

pub(crate) fn map_io_error(err: std::io::Error) -> PyErr {
    err.into()
}
