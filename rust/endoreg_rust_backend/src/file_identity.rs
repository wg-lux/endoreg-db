use crate::errors::map_io_error;
use pyo3::exceptions::{PyRuntimeError, PyValueError};
use pyo3::prelude::*;
use pyo3_stub_gen::derive::{gen_stub_pyclass, gen_stub_pymethods};
use rayon::prelude::*;
use rayon::{ThreadPool, ThreadPoolBuilder};
use sha2::{Digest, Sha256};
use std::fs::{Metadata, OpenOptions};
use std::io::{BufReader, BufWriter, Read, Write};
use std::os::unix::fs::MetadataExt;
use std::os::unix::fs::OpenOptionsExt;
use std::path::PathBuf;

const DEFAULT_CHUNK_SIZE: usize = 1024 * 1024;

/// Bounded native worker pool for stable identities of independent files.
///
/// Durable orchestration, publication, and cleanup remain Python service
/// responsibilities. This class owns only a private Rayon pool and performs
/// immutable, fail-closed reads.
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
    let mut buffer = vec![0_u8; chunk_size];

    loop {
        let read_count = reader.read(&mut buffer)?;
        if read_count == 0 {
            break;
        }
        hasher.update(&buffer[..read_count]);
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

fn stable_snapshot_to_path_impl(
    source_path: PathBuf,
    target_path: PathBuf,
    chunk_size: usize,
) -> Result<(u64, i128, String), std::io::Error> {
    let source = OpenOptions::new()
        .read(true)
        .custom_flags(libc::O_NOFOLLOW)
        .open(&source_path)?;
    let before = FileMetadataIdentity::from_metadata(&source.metadata()?);
    if !source.metadata()?.is_file() {
        return Err(std::io::Error::new(
            std::io::ErrorKind::InvalidInput,
            format!(
                "report snapshot source is not a regular file: {}",
                source_path.display()
            ),
        ));
    }

    let target = OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(&target_path)?;
    let result = (|| {
        let mut reader = BufReader::with_capacity(chunk_size, source);
        let mut writer = BufWriter::with_capacity(chunk_size, target);
        let mut hasher = Sha256::new();
        let mut buffer = vec![0_u8; chunk_size];
        let mut copied_bytes = 0_u64;

        loop {
            let read_count = reader.read(&mut buffer)?;
            if read_count == 0 {
                break;
            }
            writer.write_all(&buffer[..read_count])?;
            hasher.update(&buffer[..read_count]);
            copied_bytes = copied_bytes
                .checked_add(read_count as u64)
                .ok_or_else(|| std::io::Error::other("report snapshot size overflow"))?;
        }

        writer.flush()?;
        writer.get_ref().sync_all()?;

        let after_read = FileMetadataIdentity::from_metadata(&reader.get_ref().metadata()?);
        let current_path_metadata = std::fs::symlink_metadata(&source_path)?;
        if !current_path_metadata.is_file() {
            return Err(changed_during_read_error(&source_path));
        }
        let current_path = FileMetadataIdentity::from_metadata(&current_path_metadata);
        if before != after_read || after_read != current_path {
            return Err(changed_during_read_error(&source_path));
        }
        if copied_bytes != after_read.size_bytes {
            return Err(std::io::Error::new(
                std::io::ErrorKind::UnexpectedEof,
                format!(
                    "report snapshot byte count differs from source size: copied={copied_bytes} expected={}",
                    after_read.size_bytes
                ),
            ));
        }

        Ok((
            after_read.size_bytes,
            after_read.modified_time_ns,
            format!("{:x}", hasher.finalize()),
        ))
    })();

    if result.is_err() {
        let _ = std::fs::remove_file(&target_path);
    }
    result
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

#[pyfunction]
#[pyo3(signature = (source_path, target_path, chunk_size=DEFAULT_CHUNK_SIZE))]
pub(crate) fn stable_snapshot_to_path(
    py: Python<'_>,
    source_path: PathBuf,
    target_path: PathBuf,
    chunk_size: usize,
) -> PyResult<(u64, i128, String)> {
    if chunk_size == 0 {
        return Err(PyValueError::new_err(
            "chunk_size must be greater than zero",
        ));
    }

    py.allow_threads(move || stable_snapshot_to_path_impl(source_path, target_path, chunk_size))
        .map_err(map_io_error)
}

#[cfg(test)]
mod tests {
    use super::{
        stable_file_identity_impl, stable_snapshot_to_path_impl, BatchProcessor, DEFAULT_CHUNK_SIZE,
    };
    use sha2::{Digest, Sha256};
    use std::fs;
    use std::os::unix::fs::symlink;
    use std::time::{SystemTime, UNIX_EPOCH};

    #[test]
    fn returns_size_time_and_digest_for_stable_file() {
        let unique = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .expect("clock must follow epoch")
            .as_nanos();
        let path = std::env::temp_dir().join(format!(
            "endoreg-rust-file-identity-{}-{unique}",
            std::process::id()
        ));
        let payload = b"stable-video-source";
        fs::write(&path, payload).expect("test file must be writable");

        let (size_bytes, _modified_time_ns, digest) =
            stable_file_identity_impl(path.clone(), 4).expect("identity must succeed");

        assert_eq!(size_bytes, payload.len() as u64);
        assert_eq!(digest, format!("{:x}", Sha256::digest(payload)));
        fs::remove_file(path).expect("test file must be removable");
    }

    #[test]
    fn batch_processor_is_send_and_sync() {
        fn assert_thread_safe<T: Send + Sync>() {}
        assert_thread_safe::<BatchProcessor>();
    }

    #[test]
    fn batch_processor_uses_requested_worker_count() {
        let processor = BatchProcessor::new(2).expect("thread pool must build");
        assert_eq!(processor.worker_count(), 2);
    }

    #[test]
    fn snapshots_and_hashes_the_same_source_bytes() {
        let unique = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .expect("clock must follow epoch")
            .as_nanos();
        let source_path = std::env::temp_dir().join(format!(
            "endoreg-rust-report-source-{}-{unique}",
            std::process::id()
        ));
        let target_path = std::env::temp_dir().join(format!(
            "endoreg-rust-report-snapshot-{}-{unique}",
            std::process::id()
        ));
        let payload = b"%PDF-1.4\nstable-report\n%%EOF\n";
        fs::write(&source_path, payload).expect("test source must be writable");

        let (size_bytes, _modified_time_ns, digest) =
            stable_snapshot_to_path_impl(source_path.clone(), target_path.clone(), 5)
                .expect("snapshot must succeed");

        assert_eq!(size_bytes, payload.len() as u64);
        assert_eq!(digest, format!("{:x}", Sha256::digest(payload)));
        assert_eq!(
            fs::read(&target_path).expect("snapshot must be readable"),
            payload
        );
        fs::remove_file(source_path).expect("test source must be removable");
        fs::remove_file(target_path).expect("test snapshot must be removable");
    }

    #[test]
    fn refuses_to_overwrite_an_existing_snapshot_target() {
        let unique = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .expect("clock must follow epoch")
            .as_nanos();
        let source_path = std::env::temp_dir().join(format!(
            "endoreg-rust-report-source-existing-{}-{unique}",
            std::process::id()
        ));
        let target_path = std::env::temp_dir().join(format!(
            "endoreg-rust-report-target-existing-{}-{unique}",
            std::process::id()
        ));
        fs::write(&source_path, b"source").expect("test source must be writable");
        fs::write(&target_path, b"existing").expect("test target must be writable");

        let error = stable_snapshot_to_path_impl(
            source_path.clone(),
            target_path.clone(),
            DEFAULT_CHUNK_SIZE,
        )
        .expect_err("existing target must be rejected");

        assert_eq!(error.kind(), std::io::ErrorKind::AlreadyExists);
        assert_eq!(
            fs::read(&target_path).expect("existing target must remain readable"),
            b"existing"
        );
        fs::remove_file(source_path).expect("test source must be removable");
        fs::remove_file(target_path).expect("test target must be removable");
    }

    #[test]
    fn refuses_a_symbolic_link_source() {
        let unique = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .expect("clock must follow epoch")
            .as_nanos();
        let source_path = std::env::temp_dir().join(format!(
            "endoreg-rust-report-source-link-target-{}-{unique}",
            std::process::id()
        ));
        let link_path = std::env::temp_dir().join(format!(
            "endoreg-rust-report-source-link-{}-{unique}",
            std::process::id()
        ));
        let target_path = std::env::temp_dir().join(format!(
            "endoreg-rust-report-link-snapshot-{}-{unique}",
            std::process::id()
        ));
        fs::write(&source_path, b"source").expect("test source must be writable");
        symlink(&source_path, &link_path).expect("test link must be creatable");

        let error = stable_snapshot_to_path_impl(link_path.clone(), target_path.clone(), 4)
            .expect_err("symbolic link source must be rejected");

        assert_eq!(error.raw_os_error(), Some(libc::ELOOP));
        assert!(!target_path.exists());
        fs::remove_file(link_path).expect("test link must be removable");
        fs::remove_file(source_path).expect("test source must be removable");
    }

    #[test]
    fn stable_identity_refuses_a_symbolic_link_source() {
        let unique = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .expect("clock must follow epoch")
            .as_nanos();
        let source_path = std::env::temp_dir().join(format!(
            "endoreg-rust-identity-source-{}-{unique}",
            std::process::id()
        ));
        let link_path = std::env::temp_dir().join(format!(
            "endoreg-rust-identity-link-{}-{unique}",
            std::process::id()
        ));
        fs::write(&source_path, b"video").expect("test source must be writable");
        symlink(&source_path, &link_path).expect("test symlink must be creatable");

        let error = stable_file_identity_impl(link_path.clone(), DEFAULT_CHUNK_SIZE)
            .expect_err("symbolic link must be rejected");

        assert_eq!(error.raw_os_error(), Some(libc::ELOOP));
        fs::remove_file(link_path).expect("test link must be removable");
        fs::remove_file(source_path).expect("test source must be removable");
    }
}
