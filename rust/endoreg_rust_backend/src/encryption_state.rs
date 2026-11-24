use crate::errors::map_io_error;
use pyo3::prelude::*;
use std::fs::OpenOptions;
use std::io::{ErrorKind, Read, Seek, SeekFrom};
use std::os::unix::fs::OpenOptionsExt;
use std::path::PathBuf;

pub(crate) const LX_ENCRYPTED_MAGIC: &[u8; 8] = b"LXENC01\n";

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

pub(crate) fn has_encryption_magic(
    source: &mut (impl Read + Seek),
) -> Result<bool, std::io::Error> {
    let position = source.stream_position()?;
    let mut buffer = [0_u8; LX_ENCRYPTED_MAGIC.len()];
    let result = match source.read_exact(&mut buffer) {
        Ok(()) => Ok(&buffer == LX_ENCRYPTED_MAGIC),
        Err(error) if error.kind() == ErrorKind::UnexpectedEof => Ok(false),
        Err(error) => Err(error),
    };
    source.seek(SeekFrom::Start(position))?;
    result
}

fn encryption_status_impl(path: PathBuf) -> Result<EncryptionStatus, std::io::Error> {
    let mut file = OpenOptions::new()
        .read(true)
        .custom_flags(libc::O_NONBLOCK)
        .open(path)?;
    if !file.metadata()?.is_file() {
        return Err(std::io::Error::new(
            ErrorKind::InvalidInput,
            "Encryption probes require a regular file",
        ));
    }
    if has_encryption_magic(&mut file)? {
        Ok(EncryptionStatus::Encrypted)
    } else {
        Ok(EncryptionStatus::Plaintext)
    }
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

#[cfg(test)]
mod tests {
    use super::{has_encryption_magic, LX_ENCRYPTED_MAGIC};
    use std::io::{Cursor, Read, Seek, SeekFrom};

    struct ShortReader(Cursor<Vec<u8>>);

    impl Read for ShortReader {
        fn read(&mut self, buffer: &mut [u8]) -> std::io::Result<usize> {
            let length = buffer.len().min(1);
            self.0.read(&mut buffer[..length])
        }
    }

    impl Seek for ShortReader {
        fn seek(&mut self, position: SeekFrom) -> std::io::Result<u64> {
            self.0.seek(position)
        }
    }

    #[test]
    fn probe_handles_short_reads_and_preserves_position() {
        let mut reader = ShortReader(Cursor::new(LX_ENCRYPTED_MAGIC.to_vec()));
        assert!(has_encryption_magic(&mut reader).unwrap());
        assert_eq!(reader.stream_position().unwrap(), 0);
        reader.seek(SeekFrom::Start(1)).unwrap();
        assert!(!has_encryption_magic(&mut reader).unwrap());
        assert_eq!(reader.stream_position().unwrap(), 1);
    }

    #[test]
    fn probe_does_not_consume_plaintext_prefix() {
        for payload in [b"".as_slice(), b"LXENC01", b"plain text content"] {
            let mut reader = Cursor::new(payload);
            assert!(!has_encryption_magic(&mut reader).unwrap());
            assert_eq!(reader.stream_position().unwrap(), 0);
        }
    }
}
