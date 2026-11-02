//! Shared validation for the chunked encrypted media format (versions one and two).
//!
//! Version two authenticates the final chunk, so whole-chunk truncation or
//! extension fails; selects the wrapping key by an explicit key identifier; and
//! binds the wrapped data key to an authenticated random file identifier.
use crate::encryption_state::LX_ENCRYPTED_MAGIC;
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
use sha2::{Digest, Sha256};
use std::io::Read;

pub(crate) const WRAP_AAD: &[u8] = b"lx-annotate:dek-wrap:v1";
pub(crate) const WRAP_AAD_V2_PREFIX: &[u8] = b"lx-annotate:dek-wrap:v2";
pub(crate) const CHUNK_AAD_V2_PREFIX: &[u8] = b"lx-annotate:chunk:v2";
pub(crate) const KEY_ID_DOMAIN: &[u8] = b"lx-annotate:master-key-id:v2\x00";
pub(crate) const MAX_HEADER_BYTES: usize = 64 * 1024;
pub(crate) const MAX_CHUNK_BYTES: u64 = 64 * 1024 * 1024;
pub(crate) const AUTHENTICATION_TAG_BYTES: u64 = 16;
const ALGORITHM_V1: &str = "AESGCM-chunked-v1";
const ALGORITHM_V2: &str = "AESGCM-chunked-v2";

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct HeaderPayloadV1 {
    version: u64,
    algorithm: String,
    chunk_size: u64,
    wrapped_dek: String,
    wrap_nonce: String,
    nonce_prefix: String,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct HeaderPayloadV2 {
    version: u64,
    algorithm: String,
    chunk_size: u64,
    wrapped_dek: String,
    wrap_nonce: String,
    nonce_prefix: String,
    key_id: String,
    file_id: String,
}

#[derive(Debug)]
pub(crate) struct EncryptedHeader {
    pub(crate) version: u64,
    pub(crate) chunk_size: u64,
    pub(crate) wrapped_dek: [u8; 48],
    pub(crate) wrap_nonce: [u8; 12],
    pub(crate) nonce_prefix: [u8; 8],
    pub(crate) key_id: Option<[u8; 16]>,
    pub(crate) file_id: Option<[u8; 16]>,
}

impl EncryptedHeader {
    /// Version two requires an authenticated terminal chunk.
    pub(crate) fn authenticates_end(&self) -> bool {
        self.version == 2
    }
}

pub(crate) fn invalid_data(message: impl Into<String>) -> std::io::Error {
    std::io::Error::new(std::io::ErrorKind::InvalidData, message.into())
}

fn decode_field<const N: usize>(value: &str) -> std::io::Result<[u8; N]> {
    let decoded = URL_SAFE
        .decode(value)
        .map_err(|_| invalid_data("encrypted header base64 is invalid"))?;
    if URL_SAFE.encode(&decoded) != value {
        return Err(invalid_data("encrypted header base64 is not canonical"));
    }
    decoded
        .try_into()
        .map_err(|_| invalid_data("encrypted header key material has invalid length"))
}

fn validate_chunk_size(chunk_size: u64) -> std::io::Result<()> {
    if chunk_size == 0 || chunk_size > MAX_CHUNK_BYTES {
        return Err(invalid_data(
            "encrypted chunk size exceeds the bounded contract",
        ));
    }
    Ok(())
}

pub(crate) fn parse_header(data: &[u8]) -> std::io::Result<EncryptedHeader> {
    if data.is_empty() || data.len() > MAX_HEADER_BYTES {
        return Err(invalid_data("encrypted header length is invalid"));
    }
    // Each version is a closed schema: unknown, missing or duplicate fields fail.
    if let Ok(payload) = serde_json::from_slice::<HeaderPayloadV2>(data) {
        if payload.version != 2 || payload.algorithm != ALGORITHM_V2 {
            return Err(invalid_data("unsupported encrypted header contract"));
        }
        validate_chunk_size(payload.chunk_size)?;
        return Ok(EncryptedHeader {
            version: 2,
            chunk_size: payload.chunk_size,
            wrapped_dek: decode_field(&payload.wrapped_dek)?,
            wrap_nonce: decode_field(&payload.wrap_nonce)?,
            nonce_prefix: decode_field(&payload.nonce_prefix)?,
            key_id: Some(decode_field(&payload.key_id)?),
            file_id: Some(decode_field(&payload.file_id)?),
        });
    }
    let payload: HeaderPayloadV1 = serde_json::from_slice(data)
        .map_err(|_| invalid_data("encrypted header JSON is invalid"))?;
    if payload.version != 1 || payload.algorithm != ALGORITHM_V1 {
        return Err(invalid_data("unsupported encrypted header contract"));
    }
    validate_chunk_size(payload.chunk_size)?;
    Ok(EncryptedHeader {
        version: 1,
        chunk_size: payload.chunk_size,
        wrapped_dek: decode_field(&payload.wrapped_dek)?,
        wrap_nonce: decode_field(&payload.wrap_nonce)?,
        nonce_prefix: decode_field(&payload.nonce_prefix)?,
        key_id: None,
        file_id: None,
    })
}

pub(crate) fn read_header(source: &mut impl Read) -> std::io::Result<(EncryptedHeader, Vec<u8>)> {
    let mut magic = [0_u8; 8];
    source.read_exact(&mut magic)?;
    if &magic != LX_ENCRYPTED_MAGIC {
        return Err(invalid_data("unsupported encrypted file format"));
    }
    let mut length = [0_u8; 4];
    source.read_exact(&mut length)?;
    let size = u32::from_be_bytes(length) as usize;
    if size == 0 || size > MAX_HEADER_BYTES {
        return Err(invalid_data("encrypted header length is invalid"));
    }
    let mut encoded = vec![0_u8; size];
    source.read_exact(&mut encoded)?;
    Ok((parse_header(&encoded)?, encoded))
}

/// Non-secret identifier of a master key: a domain-separated SHA-256 prefix.
pub(crate) fn master_key_id(key: &[u8]) -> [u8; 16] {
    let mut hasher = Sha256::new();
    hasher.update(KEY_ID_DOMAIN);
    hasher.update(key);
    let digest = hasher.finalize();
    let mut identifier = [0_u8; 16];
    identifier.copy_from_slice(&digest[..16]);
    identifier
}

/// Associated data for one chunk; version two authenticates whether it is final.
pub(crate) fn chunk_aad(header: &EncryptedHeader, header_bytes: &[u8], is_final: bool) -> Vec<u8> {
    if !header.authenticates_end() {
        return header_bytes.to_vec();
    }
    let mut aad = Vec::with_capacity(CHUNK_AAD_V2_PREFIX.len() + 1 + header_bytes.len());
    aad.extend_from_slice(CHUNK_AAD_V2_PREFIX);
    aad.push(u8::from(is_final));
    aad.extend_from_slice(header_bytes);
    aad
}

/// Validate a chunk's ciphertext length for its position.
///
/// Every non-final chunk is full. A final chunk carries at least one byte, except
/// that a version-two empty file consists of exactly one empty terminal chunk.
pub(crate) fn validate_chunk_length(
    header: &EncryptedHeader,
    counter: u64,
    ciphertext_length: u64,
    is_final: bool,
) -> std::io::Result<()> {
    let full = header.chunk_size + AUTHENTICATION_TAG_BYTES;
    let valid = if is_final {
        let minimum = if header.authenticates_end() && counter == 0 {
            AUTHENTICATION_TAG_BYTES
        } else {
            AUTHENTICATION_TAG_BYTES + 1
        };
        (minimum..=full).contains(&ciphertext_length)
    } else {
        ciphertext_length == full
    };
    if !valid {
        return Err(invalid_data(
            "encrypted chunk length does not match chunk geometry",
        ));
    }
    Ok(())
}

pub(crate) fn chunk_nonce(header: &EncryptedHeader, counter: u64) -> std::io::Result<Vec<u8>> {
    let sequence =
        u32::try_from(counter).map_err(|_| invalid_data("encrypted chunk counter overflowed"))?;
    let mut nonce = header.nonce_prefix.to_vec();
    nonce.extend_from_slice(&sequence.to_be_bytes());
    Ok(nonce)
}

pub(crate) fn decrypt_aes_gcm(
    key: &[u8],
    nonce: &[u8],
    ciphertext: &[u8],
    aad: &[u8],
) -> std::io::Result<Vec<u8>> {
    if nonce.len() != 12 {
        return Err(invalid_data("AES-GCM nonce must contain 12 bytes"));
    }
    let nonce = Nonce::from_slice(nonce);
    let payload = Payload {
        msg: ciphertext,
        aad,
    };
    let plaintext = match key.len() {
        16 => AesGcm::<Aes128, U12>::new_from_slice(key)
            .map_err(|_| invalid_data("invalid key"))?
            .decrypt(nonce, payload),
        24 => AesGcm::<Aes192, U12>::new_from_slice(key)
            .map_err(|_| invalid_data("invalid key"))?
            .decrypt(nonce, payload),
        32 => AesGcm::<Aes256, U12>::new_from_slice(key)
            .map_err(|_| invalid_data("invalid key"))?
            .decrypt(nonce, payload),
        _ => return Err(invalid_data("AES-GCM key must contain 16, 24, or 32 bytes")),
    };
    plaintext.map_err(|_| invalid_data("AES-GCM authentication failed"))
}

/// Unwrap with one explicit key; version two requires its key identifier to match.
pub(crate) fn unwrap_data_key_with(
    header: &EncryptedHeader,
    key: &[u8],
) -> std::io::Result<Vec<u8>> {
    let dek = match (header.key_id, header.file_id) {
        (Some(key_id), Some(file_id)) => {
            if master_key_id(key) != key_id {
                return Err(invalid_data(
                    "No configured generation authenticated the file",
                ));
            }
            let mut aad = Vec::with_capacity(WRAP_AAD_V2_PREFIX.len() + 32);
            aad.extend_from_slice(WRAP_AAD_V2_PREFIX);
            aad.extend_from_slice(&key_id);
            aad.extend_from_slice(&file_id);
            decrypt_aes_gcm(key, &header.wrap_nonce, &header.wrapped_dek, &aad)?
        }
        _ => decrypt_aes_gcm(key, &header.wrap_nonce, &header.wrapped_dek, WRAP_AAD)?,
    };
    if dek.len() != 32 {
        return Err(invalid_data(
            "unwrapped data encryption key has invalid length",
        ));
    }
    Ok(dek)
}

/// Unwrap with the configured generations. Version two selects exactly one key
/// by identifier; version one keeps its legacy trial authentication.
pub(crate) fn unwrap_data_key(
    header: &EncryptedHeader,
    keys: &[Vec<u8>],
) -> std::io::Result<Vec<u8>> {
    if let Some(key_id) = header.key_id {
        if let Some(key) = keys.iter().find(|key| master_key_id(key) == key_id) {
            return unwrap_data_key_with(header, key);
        }
    } else {
        for key in keys {
            if let Ok(dek) = unwrap_data_key_with(header, key) {
                return Ok(dek);
            }
        }
    }
    Err(invalid_data(
        "No configured generation authenticated the file",
    ))
}

type ParsedHeaderTuple = (
    u64,
    String,
    u64,
    Py<PyBytes>,
    Py<PyBytes>,
    Py<PyBytes>,
    Option<Py<PyBytes>>,
    Option<Py<PyBytes>>,
);

#[pyfunction]
pub(crate) fn parse_encrypted_header_metadata(
    py: Python<'_>,
    data: &[u8],
) -> PyResult<ParsedHeaderTuple> {
    let header = parse_header(data).map_err(|error| PyValueError::new_err(error.to_string()))?;
    let algorithm = if header.version == 2 {
        ALGORITHM_V2
    } else {
        ALGORITHM_V1
    };
    Ok((
        header.version,
        algorithm.into(),
        header.chunk_size,
        PyBytes::new_bound(py, &header.wrapped_dek).unbind(),
        PyBytes::new_bound(py, &header.wrap_nonce).unbind(),
        PyBytes::new_bound(py, &header.nonce_prefix).unbind(),
        header
            .key_id
            .map(|value| PyBytes::new_bound(py, &value).unbind()),
        header
            .file_id
            .map(|value| PyBytes::new_bound(py, &value).unbind()),
    ))
}

// Preserve the original six-field API while sharing the same parser.
#[pyfunction]
pub(crate) fn parse_encrypted_header(
    py: Python<'_>,
    data: &[u8],
) -> PyResult<(u64, String, u64, Py<PyBytes>, Py<PyBytes>, Py<PyBytes>)> {
    let (version, algorithm, size, dek, wrap_nonce, nonce_prefix, _, _) =
        parse_encrypted_header_metadata(py, data)?;
    Ok((version, algorithm, size, dek, wrap_nonce, nonce_prefix))
}

#[pyfunction]
pub(crate) fn encrypted_chunk_context(
    py: Python<'_>,
    data: &[u8],
    counter: u64,
    ciphertext_length: u64,
    r#final: bool,
) -> PyResult<(Py<PyBytes>, Py<PyBytes>)> {
    let convert = |error: std::io::Error| PyValueError::new_err(error.to_string());
    let header = parse_header(data).map_err(convert)?;
    validate_chunk_length(&header, counter, ciphertext_length, r#final).map_err(convert)?;
    let nonce = chunk_nonce(&header, counter).map_err(convert)?;
    let aad = chunk_aad(&header, data, r#final);
    Ok((
        PyBytes::new_bound(py, &nonce).unbind(),
        PyBytes::new_bound(py, &aad).unbind(),
    ))
}
