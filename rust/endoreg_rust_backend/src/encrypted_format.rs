//! Shared validation for the version-one chunked encrypted media format.
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
use std::io::Read;

pub(crate) const WRAP_AAD: &[u8] = b"lx-annotate:dek-wrap:v1";
pub(crate) const MAX_HEADER_BYTES: usize = 64 * 1024;
pub(crate) const MAX_CHUNK_BYTES: u64 = 64 * 1024 * 1024;

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct HeaderPayload {
    version: u64,
    algorithm: String,
    chunk_size: u64,
    wrapped_dek: String,
    wrap_nonce: String,
    nonce_prefix: String,
}

#[derive(Debug)]
pub(crate) struct EncryptedHeader {
    pub(crate) chunk_size: u64,
    pub(crate) wrapped_dek: [u8; 48],
    pub(crate) wrap_nonce: [u8; 12],
    pub(crate) nonce_prefix: [u8; 8],
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

pub(crate) fn parse_header(data: &[u8]) -> std::io::Result<EncryptedHeader> {
    if data.is_empty() || data.len() > MAX_HEADER_BYTES {
        return Err(invalid_data("encrypted header length is invalid"));
    }
    let payload: HeaderPayload = serde_json::from_slice(data)
        .map_err(|_| invalid_data("encrypted header JSON is invalid"))?;
    if payload.version != 1 || payload.algorithm != "AESGCM-chunked-v1" {
        return Err(invalid_data("unsupported encrypted header contract"));
    }
    if payload.chunk_size == 0 || payload.chunk_size > MAX_CHUNK_BYTES {
        return Err(invalid_data(
            "encrypted chunk size exceeds the bounded contract",
        ));
    }
    Ok(EncryptedHeader {
        chunk_size: payload.chunk_size,
        wrapped_dek: decode_field(&payload.wrapped_dek)?,
        wrap_nonce: decode_field(&payload.wrap_nonce)?,
        nonce_prefix: decode_field(&payload.nonce_prefix)?,
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

pub(crate) fn unwrap_data_key(
    header: &EncryptedHeader,
    keys: &[Vec<u8>],
) -> std::io::Result<Vec<u8>> {
    for key in keys {
        if let Ok(dek) = decrypt_aes_gcm(key, &header.wrap_nonce, &header.wrapped_dek, WRAP_AAD) {
            if dek.len() == 32 {
                return Ok(dek);
            }
        }
    }
    Err(invalid_data(
        "No configured generation authenticated the file",
    ))
}

#[pyfunction]
pub(crate) fn parse_encrypted_header(
    py: Python<'_>,
    data: &[u8],
) -> PyResult<(u64, String, u64, Py<PyBytes>, Py<PyBytes>, Py<PyBytes>)> {
    let header = parse_header(data).map_err(|error| PyValueError::new_err(error.to_string()))?;
    Ok((
        1,
        "AESGCM-chunked-v1".into(),
        header.chunk_size,
        PyBytes::new_bound(py, &header.wrapped_dek).unbind(),
        PyBytes::new_bound(py, &header.wrap_nonce).unbind(),
        PyBytes::new_bound(py, &header.nonce_prefix).unbind(),
    ))
}
