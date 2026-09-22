use aes::{Aes128, Aes192, Aes256};
use aes_gcm::aead::consts::U12;
use aes_gcm::aead::{Aead, KeyInit, Payload};
use aes_gcm::{AesGcm, Nonce};
use base64::engine::general_purpose::URL_SAFE;
use base64::Engine;
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use pyo3::types::PyBytes;
use serde::Deserialize;
use std::fs::File;
use std::io::{Read, Seek, SeekFrom};
use std::path::PathBuf;

const MAGIC: &[u8; 8] = b"LXENC01\n";
const WRAP_AAD: &[u8] = b"lx-annotate:dek-wrap:v1";
const HEADER_LENGTH_BYTES: usize = 4;
const CHUNK_LENGTH_BYTES: usize = 4;
const AUTHENTICATION_TAG_BYTES: u64 = 16;
const NONCE_PREFIX_BYTES: usize = 8;
const CHUNK_COUNTER_BYTES: usize = 4;
const MAX_HEADER_BYTES: usize = 1024 * 1024;
const MAX_RANGE_BYTES: u64 = 8 * 1024 * 1024;
const MAX_ENCRYPTED_CHUNK_BYTES: u64 = 64 * 1024 * 1024;

type Aes128Gcm = AesGcm<Aes128, U12>;
type Aes192Gcm = AesGcm<Aes192, U12>;
type Aes256Gcm = AesGcm<Aes256, U12>;

#[derive(Debug, Deserialize)]
struct EncryptedHeader {
    version: u64,
    algorithm: String,
    chunk_size: u64,
    wrapped_dek: String,
    wrap_nonce: String,
    nonce_prefix: String,
}

#[derive(Debug)]
struct EncryptedLayout {
    header: EncryptedHeader,
    header_bytes: Vec<u8>,
    data_offset: u64,
    plaintext_size: u64,
}

fn invalid_data(message: impl Into<String>) -> std::io::Error {
    std::io::Error::new(std::io::ErrorKind::InvalidData, message.into())
}

fn decode_urlsafe(value: &str, field_name: &str) -> Result<Vec<u8>, std::io::Error> {
    URL_SAFE
        .decode(value)
        .map_err(|_| invalid_data(format!("encrypted header {field_name} is invalid")))
}

fn decrypt_aes_gcm(
    key: &[u8],
    nonce: &[u8],
    ciphertext: &[u8],
    aad: &[u8],
) -> Result<Vec<u8>, std::io::Error> {
    if nonce.len() != 12 {
        return Err(invalid_data("AES-GCM nonce must contain 12 bytes"));
    }
    let nonce = Nonce::from_slice(nonce);
    let payload = Payload {
        msg: ciphertext,
        aad,
    };
    let plaintext = match key.len() {
        16 => Aes128Gcm::new_from_slice(key)
            .map_err(|_| invalid_data("invalid 128-bit AES-GCM key"))?
            .decrypt(nonce, payload),
        24 => Aes192Gcm::new_from_slice(key)
            .map_err(|_| invalid_data("invalid 192-bit AES-GCM key"))?
            .decrypt(nonce, payload),
        32 => Aes256Gcm::new_from_slice(key)
            .map_err(|_| invalid_data("invalid 256-bit AES-GCM key"))?
            .decrypt(nonce, payload),
        _ => return Err(invalid_data("AES-GCM key must contain 16, 24, or 32 bytes")),
    };
    plaintext.map_err(|_| invalid_data("AES-GCM authentication failed"))
}

fn read_layout(file: &mut File) -> Result<EncryptedLayout, std::io::Error> {
    let mut magic = [0_u8; MAGIC.len()];
    file.read_exact(&mut magic)?;
    if &magic != MAGIC {
        return Err(invalid_data("unsupported encrypted file format"));
    }

    let mut header_length_bytes = [0_u8; HEADER_LENGTH_BYTES];
    file.read_exact(&mut header_length_bytes)?;
    let header_length = u32::from_be_bytes(header_length_bytes) as usize;
    if header_length == 0 || header_length > MAX_HEADER_BYTES {
        return Err(invalid_data("encrypted header length is invalid"));
    }
    let mut header_bytes = vec![0_u8; header_length];
    file.read_exact(&mut header_bytes)?;
    let header: EncryptedHeader = serde_json::from_slice(&header_bytes)
        .map_err(|_| invalid_data("encrypted header JSON is invalid"))?;
    if header.version != 1 || header.algorithm != "AESGCM-chunked-v1" {
        return Err(invalid_data("unsupported encrypted header contract"));
    }
    if header.chunk_size == 0 {
        return Err(invalid_data("encrypted chunk size must be positive"));
    }
    if header.chunk_size > MAX_ENCRYPTED_CHUNK_BYTES {
        return Err(invalid_data(format!(
            "encrypted chunk size exceeds {MAX_ENCRYPTED_CHUNK_BYTES} bytes"
        )));
    }
    let nonce_prefix = decode_urlsafe(&header.nonce_prefix, "nonce_prefix")?;
    if nonce_prefix.len() != NONCE_PREFIX_BYTES {
        return Err(invalid_data("encrypted nonce prefix has invalid length"));
    }

    let data_offset = file.stream_position()?;
    let encrypted_size = file.metadata()?.len();
    let payload_size = encrypted_size
        .checked_sub(data_offset)
        .ok_or_else(|| invalid_data("encrypted payload size is invalid"))?;
    if payload_size == 0 {
        return Ok(EncryptedLayout {
            header,
            header_bytes,
            data_offset,
            plaintext_size: 0,
        });
    }

    let full_ciphertext_size = header
        .chunk_size
        .checked_add(AUTHENTICATION_TAG_BYTES)
        .ok_or_else(|| invalid_data("encrypted chunk geometry overflowed"))?;
    let full_record_size = full_ciphertext_size
        .checked_add(CHUNK_LENGTH_BYTES as u64)
        .ok_or_else(|| invalid_data("encrypted record geometry overflowed"))?;
    let full_chunk_count = payload_size / full_record_size;
    let final_record_size = payload_size % full_record_size;
    let mut plaintext_size = full_chunk_count
        .checked_mul(header.chunk_size)
        .ok_or_else(|| invalid_data("plaintext size overflowed"))?;

    if final_record_size != 0 {
        if final_record_size < CHUNK_LENGTH_BYTES as u64 + AUTHENTICATION_TAG_BYTES {
            return Err(invalid_data("encrypted final chunk record is truncated"));
        }
        let final_record_offset = data_offset
            .checked_add(
                full_chunk_count
                    .checked_mul(full_record_size)
                    .ok_or_else(|| invalid_data("encrypted record offset overflowed"))?,
            )
            .ok_or_else(|| invalid_data("encrypted record offset overflowed"))?;
        file.seek(SeekFrom::Start(final_record_offset))?;
        let mut length_bytes = [0_u8; CHUNK_LENGTH_BYTES];
        file.read_exact(&mut length_bytes)?;
        let ciphertext_length = u32::from_be_bytes(length_bytes) as u64;
        if ciphertext_length + CHUNK_LENGTH_BYTES as u64 != final_record_size {
            return Err(invalid_data(
                "encrypted final chunk length does not match file size",
            ));
        }
        let final_plaintext_size = ciphertext_length
            .checked_sub(AUTHENTICATION_TAG_BYTES)
            .ok_or_else(|| invalid_data("encrypted final chunk payload is invalid"))?;
        if final_plaintext_size > header.chunk_size {
            return Err(invalid_data("encrypted final chunk payload is invalid"));
        }
        plaintext_size = plaintext_size
            .checked_add(final_plaintext_size)
            .ok_or_else(|| invalid_data("plaintext size overflowed"))?;
    }

    Ok(EncryptedLayout {
        header,
        header_bytes,
        data_offset,
        plaintext_size,
    })
}

fn decrypt_encrypted_file_range_impl(
    path: PathBuf,
    master_key: Vec<u8>,
    start: u64,
    end: u64,
) -> Result<Vec<u8>, std::io::Error> {
    if end < start {
        return Err(invalid_data("encrypted byte range end precedes start"));
    }
    let requested_length = end
        .checked_sub(start)
        .and_then(|value| value.checked_add(1))
        .ok_or_else(|| invalid_data("encrypted byte range length overflowed"))?;
    if requested_length > MAX_RANGE_BYTES {
        return Err(invalid_data(format!(
            "native encrypted byte range exceeds {MAX_RANGE_BYTES} bytes"
        )));
    }

    let mut file = File::open(path)?;
    let layout = read_layout(&mut file)?;
    if end >= layout.plaintext_size {
        return Err(invalid_data(format!(
            "requested byte range {start}-{end} exceeds plaintext size {}",
            layout.plaintext_size
        )));
    }

    let wrapped_dek = decode_urlsafe(&layout.header.wrapped_dek, "wrapped_dek")?;
    let wrap_nonce = decode_urlsafe(&layout.header.wrap_nonce, "wrap_nonce")?;
    let dek = decrypt_aes_gcm(&master_key, &wrap_nonce, &wrapped_dek, WRAP_AAD)?;
    if dek.len() != 32 {
        return Err(invalid_data(
            "unwrapped data encryption key has invalid length",
        ));
    }

    let nonce_prefix = decode_urlsafe(&layout.header.nonce_prefix, "nonce_prefix")?;
    let full_ciphertext_size = layout.header.chunk_size + AUTHENTICATION_TAG_BYTES;
    let full_record_size = CHUNK_LENGTH_BYTES as u64 + full_ciphertext_size;
    let first_counter = start / layout.header.chunk_size;
    let last_counter = end / layout.header.chunk_size;
    let mut selected = Vec::with_capacity(requested_length as usize);

    for counter in first_counter..=last_counter {
        let plaintext_offset = counter
            .checked_mul(layout.header.chunk_size)
            .ok_or_else(|| invalid_data("plaintext offset overflowed"))?;
        let plaintext_length = layout
            .header
            .chunk_size
            .min(layout.plaintext_size - plaintext_offset);
        let expected_ciphertext_length = plaintext_length + AUTHENTICATION_TAG_BYTES;
        let record_offset = layout
            .data_offset
            .checked_add(
                counter
                    .checked_mul(full_record_size)
                    .ok_or_else(|| invalid_data("encrypted record offset overflowed"))?,
            )
            .ok_or_else(|| invalid_data("encrypted record offset overflowed"))?;
        file.seek(SeekFrom::Start(record_offset))?;
        let mut length_bytes = [0_u8; CHUNK_LENGTH_BYTES];
        file.read_exact(&mut length_bytes)?;
        let ciphertext_length = u32::from_be_bytes(length_bytes) as u64;
        if ciphertext_length != expected_ciphertext_length {
            return Err(invalid_data(
                "encrypted chunk length does not match chunk geometry",
            ));
        }
        let mut ciphertext = vec![0_u8; ciphertext_length as usize];
        file.read_exact(&mut ciphertext)?;

        let counter_u32 = u32::try_from(counter)
            .map_err(|_| invalid_data("encrypted chunk counter overflowed"))?;
        let mut nonce = Vec::with_capacity(NONCE_PREFIX_BYTES + CHUNK_COUNTER_BYTES);
        nonce.extend_from_slice(&nonce_prefix);
        nonce.extend_from_slice(&counter_u32.to_be_bytes());
        let plaintext = decrypt_aes_gcm(&dek, &nonce, &ciphertext, &layout.header_bytes)?;

        let slice_start = start.saturating_sub(plaintext_offset) as usize;
        let slice_end = (end - plaintext_offset + 1).min(plaintext_length) as usize;
        selected.extend_from_slice(&plaintext[slice_start..slice_end]);
    }

    if selected.len() as u64 != requested_length {
        return Err(invalid_data("decrypted byte range length is inconsistent"));
    }
    Ok(selected)
}

// PyO3 0.22 expands this binding with a same-type PyErr conversion.
#[allow(clippy::useless_conversion)]
#[pyfunction]
pub(crate) fn decrypt_encrypted_file_range(
    py: Python<'_>,
    path: PathBuf,
    master_key: Vec<u8>,
    start: u64,
    end: u64,
) -> PyResult<Py<PyBytes>> {
    let plaintext = py
        .allow_threads(move || decrypt_encrypted_file_range_impl(path, master_key, start, end))
        .map_err(|error| PyValueError::new_err(error.to_string()))?;
    Ok(PyBytes::new_bound(py, &plaintext).unbind())
}

#[cfg(test)]
mod tests {
    use super::{decrypt_aes_gcm, decrypt_encrypted_file_range_impl, WRAP_AAD};
    use aes_gcm::aead::{Aead, KeyInit, Payload};
    use aes_gcm::{Aes256Gcm, Nonce};
    use base64::engine::general_purpose::URL_SAFE;
    use base64::Engine;
    use serde_json::json;
    use std::fs::{self, File};
    use std::io::Write;
    use std::path::PathBuf;
    use std::time::{SystemTime, UNIX_EPOCH};

    fn fixture_path(label: &str) -> PathBuf {
        let unique = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .expect("clock must follow epoch")
            .as_nanos();
        std::env::temp_dir().join(format!(
            "endoreg-rust-{label}-{}-{unique}",
            std::process::id()
        ))
    }

    fn write_encrypted_fixture(path: &PathBuf, plaintext: &[u8], master_key: &[u8]) {
        let chunk_size = 32_usize;
        let dek = [7_u8; 32];
        let wrap_nonce = [3_u8; 12];
        let nonce_prefix = [9_u8; 8];
        let wrapped_dek = Aes256Gcm::new_from_slice(master_key)
            .expect("master key must be valid")
            .encrypt(
                Nonce::from_slice(&wrap_nonce),
                Payload {
                    msg: &dek,
                    aad: WRAP_AAD,
                },
            )
            .expect("data encryption key wrapping must succeed");
        let header_bytes = serde_json::to_vec(&json!({
            "algorithm": "AESGCM-chunked-v1",
            "chunk_size": chunk_size,
            "nonce_prefix": URL_SAFE.encode(nonce_prefix),
            "version": 1,
            "wrap_nonce": URL_SAFE.encode(wrap_nonce),
            "wrapped_dek": URL_SAFE.encode(wrapped_dek),
        }))
        .expect("header must serialize");
        let cipher = Aes256Gcm::new_from_slice(&dek).expect("data key must be valid");
        let mut file = File::create(path).expect("fixture file must be created");
        file.write_all(b"LXENC01\n").expect("magic must write");
        file.write_all(&(header_bytes.len() as u32).to_be_bytes())
            .expect("header length must write");
        file.write_all(&header_bytes).expect("header must write");
        for (counter, chunk) in plaintext.chunks(chunk_size).enumerate() {
            let mut nonce = nonce_prefix.to_vec();
            nonce.extend_from_slice(&(counter as u32).to_be_bytes());
            let ciphertext = cipher
                .encrypt(
                    Nonce::from_slice(&nonce),
                    Payload {
                        msg: chunk,
                        aad: &header_bytes,
                    },
                )
                .expect("chunk encryption must succeed");
            file.write_all(&(ciphertext.len() as u32).to_be_bytes())
                .expect("chunk length must write");
            file.write_all(&ciphertext).expect("chunk must write");
        }
    }

    #[test]
    fn decrypts_cross_chunk_range_byte_exactly() {
        let path = fixture_path("range");
        let master_key = [5_u8; 32];
        let plaintext: Vec<u8> = (0..101).map(|value| (value % 251) as u8).collect();
        write_encrypted_fixture(&path, &plaintext, &master_key);

        let selected = decrypt_encrypted_file_range_impl(path.clone(), master_key.to_vec(), 29, 77)
            .expect("native range decryption must succeed");

        assert_eq!(selected, plaintext[29..=77]);
        fs::remove_file(path).expect("fixture must be removable");
    }

    #[test]
    fn rejects_wrong_master_key() {
        let path = fixture_path("wrong-key");
        let master_key = [5_u8; 32];
        write_encrypted_fixture(&path, b"clinical video bytes", &master_key);

        let error = decrypt_encrypted_file_range_impl(path.clone(), vec![6_u8; 32], 0, 4)
            .expect_err("wrong key must fail closed");

        assert!(error.to_string().contains("authentication failed"));
        fs::remove_file(path).expect("fixture must be removable");
    }

    #[test]
    fn aes_helper_accepts_all_supported_master_key_lengths() {
        let nonce = [4_u8; 12];
        for key in [vec![1_u8; 16], vec![2_u8; 24], vec![3_u8; 32]] {
            let ciphertext = match key.len() {
                16 => super::Aes128Gcm::new_from_slice(&key)
                    .expect("key must be valid")
                    .encrypt(Nonce::from_slice(&nonce), b"dek".as_slice())
                    .expect("encryption must succeed"),
                24 => super::Aes192Gcm::new_from_slice(&key)
                    .expect("key must be valid")
                    .encrypt(Nonce::from_slice(&nonce), b"dek".as_slice())
                    .expect("encryption must succeed"),
                32 => super::Aes256Gcm::new_from_slice(&key)
                    .expect("key must be valid")
                    .encrypt(Nonce::from_slice(&nonce), b"dek".as_slice())
                    .expect("encryption must succeed"),
                _ => unreachable!(),
            };
            assert_eq!(
                decrypt_aes_gcm(&key, &nonce, &ciphertext, b"").expect("decrypt must work"),
                b"dek"
            );
        }
    }
}
