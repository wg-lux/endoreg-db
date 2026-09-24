use crate::encrypted_format::{
    decrypt_aes_gcm, invalid_data, read_header, unwrap_data_key, MAX_CHUNK_BYTES,
};
use crate::encryption_state::has_encryption_magic;
use crate::errors::map_io_error;
use pyo3::exceptions::{PyRuntimeError, PyValueError};
use pyo3::prelude::*;
use pyo3_stub_gen::derive::{gen_stub_pyclass, gen_stub_pymethods};
use rayon::prelude::*;
use rayon::{ThreadPool, ThreadPoolBuilder};
use sha2::{Digest, Sha256};
use std::fs::{Metadata, OpenOptions};
use std::io::{BufReader, Read};
use std::os::unix::fs::MetadataExt;
use std::os::unix::fs::OpenOptionsExt;
use std::path::PathBuf;

const DEFAULT_CHUNK_SIZE: usize = 1024 * 1024;
#[gen_stub_pyclass]
#[pyclass(frozen)]
pub(crate) struct BatchProcessor {
    worker_count: usize,
    pool: ThreadPool,
}

fn assert_send_sync<T: Send + Sync>() {}
const _: fn() = assert_send_sync::<BatchProcessor>;

#[gen_stub_pymethods]
#[pymethods]
impl BatchProcessor {
    #[new]
    pub(crate) fn new(worker_count: usize) -> PyResult<Self> {
        if worker_count == 0 {
            return Err(PyValueError::new_err(
                "worker_count must be greater than zero",
            ));
        }
        let pool = ThreadPoolBuilder::new()
            .num_threads(worker_count)
            .thread_name(|index| format!("endoreg-batch-{index}"))
            .build()
            .map_err(|error| {
                PyRuntimeError::new_err(format!("failed to create Rayon batch processor: {error}"))
            })?;
        Ok(Self { worker_count, pool })
    }

    #[getter]
    pub(crate) fn worker_count(&self) -> usize {
        self.worker_count
    }

    #[pyo3(signature = (paths, chunk_size=DEFAULT_CHUNK_SIZE, master_keys=Vec::new()))]
    pub(crate) fn stable_file_identities(
        &self,
        py: Python<'_>,
        paths: Vec<PathBuf>,
        chunk_size: usize,
        master_keys: Vec<Vec<u8>>,
    ) -> PyResult<Vec<(u64, i128, String)>> {
        if chunk_size == 0 || chunk_size as u64 > MAX_CHUNK_BYTES {
            return Err(PyValueError::new_err(
                "chunk_size must be greater than zero",
            ));
        }

        py.allow_threads(|| {
            self.pool.install(|| {
                paths
                    .into_par_iter()
                    .map(|path| stable_file_identity_impl(path, chunk_size, &master_keys))
                    .collect::<Result<Vec<_>, _>>()
            })
        })
        .map_err(map_io_error)
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
struct FileMetadataIdentity {
    device: u64,
    inode: u64,
    size_bytes: u64,
    modified_time_ns: i128,
}

impl FileMetadataIdentity {
    fn from_metadata(metadata: &Metadata) -> Self {
        Self {
            device: metadata.dev(),
            inode: metadata.ino(),
            size_bytes: metadata.len(),
            modified_time_ns: i128::from(metadata.mtime()) * 1_000_000_000
                + i128::from(metadata.mtime_nsec()),
        }
    }
}

fn changed_during_read_error(path: &PathBuf) -> std::io::Error {
    std::io::Error::new(
        std::io::ErrorKind::InvalidData,
        format!(
            "file changed or was replaced while deriving stable identity: {}",
            path.display()
        ),
    )
}

fn stable_file_identity_impl(
    path: PathBuf,
    chunk_size: usize,
    master_keys: &[Vec<u8>],
) -> Result<(u64, i128, String), std::io::Error> {
    let file = OpenOptions::new()
        .read(true)
        .custom_flags(libc::O_NOFOLLOW)
        .open(&path)?;
    if !file.metadata()?.is_file() {
        return Err(std::io::Error::new(
            std::io::ErrorKind::InvalidInput,
            format!(
                "stable identity source is not a regular file: {}",
                path.display()
            ),
        ));
    }
    let before = FileMetadataIdentity::from_metadata(&file.metadata()?);
    let mut reader = BufReader::with_capacity(chunk_size, file);
    let mut hasher = Sha256::new();

    if has_encryption_magic(&mut reader)? {
        let (header, header_bytes) = read_header(&mut reader)?;
        let dek = unwrap_data_key(&header, master_keys)?;
        let mut counter: u64 = 0;
        let mut final_chunk_seen = false;
        loop {
            let mut length = [0_u8; 4];
            match reader.read_exact(&mut length[..1]) {
                Ok(()) => {}
                Err(error) if error.kind() == std::io::ErrorKind::UnexpectedEof => break,
                Err(error) => return Err(error),
            }
            reader.read_exact(&mut length[1..])?;
            let size = u32::from_be_bytes(length) as usize;
            if final_chunk_seen || size <= 16 || size as u64 > header.chunk_size + 16 {
                return Err(invalid_data(
                    "encrypted chunk length does not match chunk geometry",
                ));
            }
            final_chunk_seen = (size as u64) < header.chunk_size + 16;
            let mut ciphertext = vec![0_u8; size];
            reader.read_exact(&mut ciphertext)?;
            let sequence = u32::try_from(counter)
                .map_err(|_| invalid_data("encrypted chunk counter overflowed"))?;
            let mut nonce = header.nonce_prefix.to_vec();
            nonce.extend_from_slice(&sequence.to_be_bytes());
            hasher.update(decrypt_aes_gcm(&dek, &nonce, &ciphertext, &header_bytes)?);
            counter += 1;
        }
    } else {
        // --- Plaintext / Raw File Path ---
        let mut buffer = vec![0_u8; chunk_size];
        loop {
            let read_count = reader.read(&mut buffer)?;
            if read_count == 0 {
                break;
            }
            hasher.update(&buffer[..read_count]);
        }
    }

    let after_read = FileMetadataIdentity::from_metadata(&reader.get_ref().metadata()?);
    let current_path_metadata = std::fs::symlink_metadata(&path)?;
    if !current_path_metadata.is_file() {
        return Err(changed_during_read_error(&path));
    }
    let current_path = FileMetadataIdentity::from_metadata(&current_path_metadata);
    if before != after_read || after_read != current_path {
        return Err(changed_during_read_error(&path));
    }

    Ok((
        after_read.size_bytes,
        after_read.modified_time_ns,
        format!("{:x}", hasher.finalize()),
    ))
}

#[pyfunction]
#[pyo3(signature = (path, chunk_size=DEFAULT_CHUNK_SIZE, master_keys=Vec::new()))]
pub(crate) fn stable_file_identity(
    py: Python<'_>,
    path: PathBuf,
    chunk_size: usize,
    master_keys: Vec<Vec<u8>>,
) -> PyResult<(u64, i128, String)> {
    if chunk_size == 0 || chunk_size as u64 > MAX_CHUNK_BYTES {
        return Err(PyValueError::new_err(
            "chunk_size must be greater than zero",
        ));
    }

    py.allow_threads(move || stable_file_identity_impl(path, chunk_size, &master_keys))
        .map_err(map_io_error)
}
