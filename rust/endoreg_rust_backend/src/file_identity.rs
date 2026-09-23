use crate::errors::map_io_error;
use aes_gcm::{
    aead::{Aead, KeyInit, Payload},
    Aes128Gcm, Aes256Gcm, Key, Nonce,
};
use base64::engine::general_purpose::{STANDARD, STANDARD_NO_PAD, URL_SAFE, URL_SAFE_NO_PAD};
use base64::Engine;
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
const MAGIC: &[u8; 8] = b"LXENC01\n";
const WRAP_AAD: &[u8] = b"lx-annotate:dek-wrap:v1";

#[derive(serde::Deserialize)]
struct EncryptedHeaderPayload {
    wrapped_dek: String,
    wrap_nonce: String,
    nonce_prefix: String,
}

enum DynamicGcm {
    Aes128(Aes128Gcm),
    Aes256(Aes256Gcm),
}

impl DynamicGcm {
    fn new(key: &[u8]) -> std::io::Result<Self> {
        match key.len() {
            16 => Ok(Self::Aes128(Aes128Gcm::new(Key::<Aes128Gcm>::from_slice(key)))),
            32 => Ok(Self::Aes256(Aes256Gcm::new(Key::<Aes256Gcm>::from_slice(key)))),
            _ => Err(std::io::Error::new(
                std::io::ErrorKind::InvalidData,
                format!("Unsupported AES key length: {} bytes", key.len()),
            )),
        }
    }

    fn decrypt(&self, nonce: &[u8], payload: Payload) -> std::io::Result<Vec<u8>> {
        let res = match self {
            Self::Aes128(c) => c.decrypt(Nonce::from_slice(nonce), payload),
            Self::Aes256(c) => c.decrypt(Nonce::from_slice(nonce), payload),
        };
        res.map_err(|e| std::io::Error::new(std::io::ErrorKind::InvalidData, e.to_string()))
    }
}

fn base64_decode(s: &str) -> std::io::Result<Vec<u8>> {
    let trimmed = s.trim();
    URL_SAFE
        .decode(trimmed)
        .or_else(|_| URL_SAFE_NO_PAD.decode(trimmed))
        .or_else(|_| STANDARD.decode(trimmed))
        .or_else(|_| STANDARD_NO_PAD.decode(trimmed))
        .map_err(|e| {
            std::io::Error::new(
                std::io::ErrorKind::InvalidData,
                format!("Base64 decode error: {e}"),
            )
        })
}

fn load_master_key() -> std::io::Result<Vec<u8>> {
    let key_text = if let Ok(val) = std::env::var("LX_ANNOTATE_MASTER_KEY") {
        val.trim().to_string()
    } else if let Ok(file_path) = std::env::var("LX_ANNOTATE_MASTER_KEY_FILE") {
        std::fs::read_to_string(file_path.trim())?.trim().to_string()
    } else {
        return Err(std::io::Error::new(
            std::io::ErrorKind::NotFound,
            "LX_ANNOTATE_MASTER_KEY or LX_ANNOTATE_MASTER_KEY_FILE environment variable not set",
        ));
    };

    let key_bytes = base64_decode(&key_text)?;
    if !matches!(key_bytes.len(), 16 | 24 | 32) {
        return Err(std::io::Error::new(
            std::io::ErrorKind::InvalidData,
            format!(
                "Master key must decode to 16, 24, or 32 bytes; got {}",
                key_bytes.len()
            ),
        ));
    }
    Ok(key_bytes)
}

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
                PyRuntimeError::new_err(format!(
                    "failed to create Rayon batch processor: {error}"
                ))
            })?;
        Ok(Self { worker_count, pool })
    }

    #[getter]
    pub(crate) fn worker_count(&self) -> usize {
        self.worker_count
    }

    #[pyo3(signature = (paths, chunk_size=DEFAULT_CHUNK_SIZE))]
    pub(crate) fn stable_file_identities(
        &self,
        py: Python<'_>,
        paths: Vec<PathBuf>,
        chunk_size: usize,
    ) -> PyResult<Vec<(u64, i128, String)>> {
        if chunk_size == 0 {
            return Err(PyValueError::new_err(
                "chunk_size must be greater than zero",
            ));
        }

        py.allow_threads(|| {
            self.pool.install(|| {
                paths
                    .into_par_iter()
                    .map(|path| stable_file_identity_impl(path, chunk_size))
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

    // Check for LXENC01 header
    let mut magic_buf = [0_u8; 8];
    let bytes_read = reader.read(&mut magic_buf)?;

    if bytes_read == 8 && &magic_buf == MAGIC {
        // --- LXENC01 Encrypted Stream Path ---
        let master_key = load_master_key()?;

        let mut len_buf = [0_u8; 4];
        reader.read_exact(&mut len_buf)?;
        let header_len = u32::from_be_bytes(len_buf) as usize;

        let mut header_bytes = vec![0_u8; header_len];
        reader.read_exact(&mut header_bytes)?;

        let header: EncryptedHeaderPayload = serde_json::from_slice(&header_bytes).map_err(
            |e| {
                std::io::Error::new(
                    std::io::ErrorKind::InvalidData,
                    format!("Invalid LXENC01 header JSON: {e}"),
                )
            },
        )?;

        let wrapped_dek = base64_decode(&header.wrapped_dek)?;
        let wrap_nonce = base64_decode(&header.wrap_nonce)?;
        let nonce_prefix = base64_decode(&header.nonce_prefix)?;

        let master_cipher = DynamicGcm::new(&master_key)?;
        let dek = master_cipher.decrypt(
            &wrap_nonce,
            Payload {
                msg: &wrapped_dek,
                aad: WRAP_AAD,
            },
        )?;

        let dek_cipher = DynamicGcm::new(&dek)?;
        let mut counter: u32 = 0;

        loop {
            let mut chunk_len_buf = [0_u8; 4];
            match reader.read_exact(&mut chunk_len_buf) {
                Ok(()) => {}
                Err(e) if e.kind() == std::io::ErrorKind::UnexpectedEof => break,
                Err(e) => return Err(e),
            }

            let chunk_len = u32::from_be_bytes(chunk_len_buf) as usize;
            let mut cipher_buf = vec![0_u8; chunk_len];
            reader.read_exact(&mut cipher_buf)?;

            let mut nonce = Vec::with_capacity(12);
            nonce.extend_from_slice(&nonce_prefix);
            nonce.extend_from_slice(&counter.to_be_bytes());

            let plaintext = dek_cipher.decrypt(
                &nonce,
                Payload {
                    msg: &cipher_buf,
                    aad: &header_bytes,
                },
            )?;

            hasher.update(&plaintext);
            counter += 1;
        }
    } else {
        // --- Plaintext / Raw File Path ---
        if bytes_read > 0 {
            hasher.update(&magic_buf[..bytes_read]);
        }
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
#[pyo3(signature = (path, chunk_size=DEFAULT_CHUNK_SIZE))]
pub(crate) fn stable_file_identity(
    py: Python<'_>,
    path: PathBuf,
    chunk_size: usize,
) -> PyResult<(u64, i128, String)> {
    if chunk_size == 0 {
        return Err(PyValueError::new_err(
            "chunk_size must be greater than zero",
        ));
    }

    py.allow_threads(move || stable_file_identity_impl(path, chunk_size))
        .map_err(map_io_error)
}