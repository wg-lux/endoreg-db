use crate::encrypted_format::{
    chunk_aad, chunk_nonce, decrypt_aes_gcm, invalid_data, read_header, unwrap_data_key_with,
    validate_chunk_length, EncryptedHeader, AUTHENTICATION_TAG_BYTES,
};
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use pyo3::types::PyBytes;
use std::fs::File;
use std::io::{Read, Seek, SeekFrom};
use std::path::PathBuf;

const CHUNK_LENGTH_BYTES: usize = 4;
const MAX_RANGE_BYTES: u64 = 8 * 1024 * 1024;

#[derive(Debug)]
struct EncryptedLayout {
    header: EncryptedHeader,
    header_bytes: Vec<u8>,
    data_offset: u64,
    plaintext_size: u64,
    chunk_count: u64,
}

fn read_layout(file: &mut File) -> Result<EncryptedLayout, std::io::Error> {
    let (header, header_bytes) = read_header(file)?;
    let data_offset = file.stream_position()?;
    let encrypted_size = file.metadata()?.len();
    let payload_size = encrypted_size
        .checked_sub(data_offset)
        .ok_or_else(|| invalid_data("encrypted payload size is invalid"))?;
    if payload_size == 0 {
        if header.authenticates_end() {
            return Err(invalid_data(
                "encrypted file lacks its authenticated terminal chunk",
            ));
        }
        return Ok(EncryptedLayout {
            header,
            header_bytes,
            data_offset,
            plaintext_size: 0,
            chunk_count: 0,
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
    let mut chunk_count = full_chunk_count;

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
        validate_chunk_length(&header, full_chunk_count, ciphertext_length, true)?;
        plaintext_size = plaintext_size
            .checked_add(ciphertext_length - AUTHENTICATION_TAG_BYTES)
            .ok_or_else(|| invalid_data("plaintext size overflowed"))?;
        chunk_count += 1;
    }

    Ok(EncryptedLayout {
        header,
        header_bytes,
        data_offset,
        plaintext_size,
        chunk_count,
    })
}

fn decrypt_record(
    file: &mut File,
    layout: &EncryptedLayout,
    dek: &[u8],
    counter: u64,
) -> Result<Vec<u8>, std::io::Error> {
    let full_record_size =
        CHUNK_LENGTH_BYTES as u64 + layout.header.chunk_size + AUTHENTICATION_TAG_BYTES;
    let plaintext_offset = counter
        .checked_mul(layout.header.chunk_size)
        .ok_or_else(|| invalid_data("plaintext offset overflowed"))?;
    let plaintext_length = layout
        .header
        .chunk_size
        .min(layout.plaintext_size - plaintext_offset);
    let is_final = counter + 1 == layout.chunk_count;
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
    validate_chunk_length(&layout.header, counter, ciphertext_length, is_final)?;
    if ciphertext_length != plaintext_length + AUTHENTICATION_TAG_BYTES {
        return Err(invalid_data(
            "encrypted chunk length does not match chunk geometry",
        ));
    }
    let mut ciphertext = vec![0_u8; ciphertext_length as usize];
    file.read_exact(&mut ciphertext)?;

    let nonce = chunk_nonce(&layout.header, counter)?;
    let aad = chunk_aad(&layout.header, &layout.header_bytes, is_final);
    decrypt_aes_gcm(dek, &nonce, &ciphertext, &aad)
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

    let dek = unwrap_data_key_with(&layout.header, &master_key)?;
    let first_counter = start / layout.header.chunk_size;
    let last_counter = end / layout.header.chunk_size;
    // A range that excludes the end must still authenticate the file's end.
    // Keep this read on the same descriptor as the selected records.
    if layout.header.authenticates_end() && last_counter + 1 < layout.chunk_count {
        decrypt_record(&mut file, &layout, &dek, layout.chunk_count - 1)?;
    }
    let mut selected = Vec::with_capacity(requested_length as usize);

    for counter in first_counter..=last_counter {
        let plaintext_offset = counter
            .checked_mul(layout.header.chunk_size)
            .ok_or_else(|| invalid_data("plaintext offset overflowed"))?;
        let plaintext_length = layout
            .header
            .chunk_size
            .min(layout.plaintext_size - plaintext_offset);
        let plaintext = decrypt_record(&mut file, &layout, &dek, counter)?;

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
    use super::decrypt_encrypted_file_range_impl;
    use crate::encrypted_format::{
        decrypt_aes_gcm, master_key_id, CHUNK_AAD_V2_PREFIX, WRAP_AAD, WRAP_AAD_V2_PREFIX,
    };
    use aes_gcm::aead::{Aead, KeyInit, Payload};
    use aes_gcm::{Aes128Gcm, Aes256Gcm, Nonce};
    type Aes192Gcm = aes_gcm::AesGcm<aes::Aes192, aes_gcm::aead::consts::U12>;
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

    fn write_v2_fixture(
        path: &PathBuf,
        plaintext: &[u8],
        master_key: &[u8],
        chunk_size: usize,
    ) -> Vec<Vec<u8>> {
        let dek = [8_u8; 32];
        let wrap_nonce = [4_u8; 12];
        let nonce_prefix = [2_u8; 8];
        let key_id = master_key_id(master_key);
        let file_id = [6_u8; 16];
        let mut wrap_aad = WRAP_AAD_V2_PREFIX.to_vec();
        wrap_aad.extend_from_slice(&key_id);
        wrap_aad.extend_from_slice(&file_id);
        let wrapped_dek = Aes256Gcm::new_from_slice(master_key)
            .expect("master key must be valid")
            .encrypt(
                Nonce::from_slice(&wrap_nonce),
                Payload {
                    msg: &dek,
                    aad: &wrap_aad,
                },
            )
            .expect("data encryption key wrapping must succeed");
        let header_bytes = serde_json::to_vec(&json!({
            "algorithm": "AESGCM-chunked-v2",
            "chunk_size": chunk_size,
            "file_id": URL_SAFE.encode(file_id),
            "key_id": URL_SAFE.encode(key_id),
            "nonce_prefix": URL_SAFE.encode(nonce_prefix),
            "version": 2,
            "wrap_nonce": URL_SAFE.encode(wrap_nonce),
            "wrapped_dek": URL_SAFE.encode(wrapped_dek),
        }))
        .expect("header must serialize");
        let cipher = Aes256Gcm::new_from_slice(&dek).expect("data key must be valid");
        let chunks: Vec<&[u8]> = if plaintext.is_empty() {
            vec![&[]]
        } else {
            plaintext.chunks(chunk_size).collect()
        };
        let mut records = Vec::new();
        let mut file = File::create(path).expect("fixture file must be created");
        file.write_all(b"LXENC01\n").expect("magic must write");
        file.write_all(&(header_bytes.len() as u32).to_be_bytes())
            .expect("header length must write");
        file.write_all(&header_bytes).expect("header must write");
        for (counter, chunk) in chunks.iter().enumerate() {
            let mut nonce = nonce_prefix.to_vec();
            nonce.extend_from_slice(&(counter as u32).to_be_bytes());
            let mut aad = CHUNK_AAD_V2_PREFIX.to_vec();
            aad.push(u8::from(counter + 1 == chunks.len()));
            aad.extend_from_slice(&header_bytes);
            let ciphertext = cipher
                .encrypt(
                    Nonce::from_slice(&nonce),
                    Payload {
                        msg: chunk,
                        aad: &aad,
                    },
                )
                .expect("chunk encryption must succeed");
            let mut record = (ciphertext.len() as u32).to_be_bytes().to_vec();
            record.extend_from_slice(&ciphertext);
            file.write_all(&record).expect("chunk must write");
            records.push(record);
        }
        records
    }

    #[test]
    fn version_two_decrypts_ranges_including_the_final_chunk() {
        let path = fixture_path("v2-range");
        let master_key = [5_u8; 32];
        let plaintext: Vec<u8> = (0..96).map(|value| (value * 3 % 251) as u8).collect();
        write_v2_fixture(&path, &plaintext, &master_key, 32);

        let selected = decrypt_encrypted_file_range_impl(path.clone(), master_key.to_vec(), 20, 95)
            .expect("version two range decryption must succeed");

        assert_eq!(selected, plaintext[20..=95]);
        fs::remove_file(path).expect("fixture must be removable");
    }

    #[test]
    fn version_two_detects_whole_chunk_truncation_and_extension() {
        let master_key = [5_u8; 32];
        let plaintext: Vec<u8> = (0..96).map(|value| value as u8).collect();
        let source = fixture_path("v2-source");
        let records = write_v2_fixture(&source, &plaintext, &master_key, 32);
        let complete = fs::read(&source).expect("fixture must be readable");
        let record_bytes: usize = records.iter().map(Vec::len).sum();

        let truncated = fixture_path("v2-truncated");
        fs::write(&truncated, &complete[..complete.len() - records[2].len()])
            .expect("truncated fixture must write");
        let error =
            decrypt_encrypted_file_range_impl(truncated.clone(), master_key.to_vec(), 32, 63)
                .expect_err("a non-final chunk presented as final must fail");
        assert!(error.to_string().contains("authentication failed"));

        let extended = fixture_path("v2-extended");
        let mut appended = complete.clone();
        appended.extend_from_slice(&records[0]);
        fs::write(&extended, &appended).expect("extended fixture must write");
        let error =
            decrypt_encrypted_file_range_impl(extended.clone(), master_key.to_vec(), 64, 95)
                .expect_err("a final chunk followed by another chunk must fail");
        assert!(error.to_string().contains("authentication failed"));

        let corrupted = fixture_path("v2-corrupted-terminal");
        let mut damaged = complete.clone();
        *damaged.last_mut().expect("fixture has a final tag") ^= 1;
        fs::write(&corrupted, &damaged).expect("corrupted fixture must write");
        for path in [&truncated, &extended, &corrupted] {
            let error = decrypt_encrypted_file_range_impl(path.clone(), master_key.to_vec(), 0, 31)
                .expect_err("an early range must authenticate the terminal record");
            assert!(error.to_string().contains("authentication failed"));
        }

        let header_only = fixture_path("v2-header-only");
        fs::write(&header_only, &complete[..complete.len() - record_bytes])
            .expect("header-only fixture must write");
        let error =
            decrypt_encrypted_file_range_impl(header_only.clone(), master_key.to_vec(), 0, 0)
                .expect_err("a missing terminal chunk must fail");
        assert!(error.to_string().contains("terminal chunk"));

        for path in [source, truncated, extended, corrupted, header_only] {
            fs::remove_file(path).expect("fixture must be removable");
        }
    }

    #[test]
    fn version_two_selects_the_key_by_identifier() {
        let path = fixture_path("v2-key-id");
        write_v2_fixture(&path, b"clinical bytes", &[5_u8; 32], 32);

        let error = decrypt_encrypted_file_range_impl(path.clone(), vec![6_u8; 32], 0, 3)
            .expect_err("a key with another identifier must not be tried");

        assert!(error.to_string().contains("No configured generation"));
        fs::remove_file(path).expect("fixture must be removable");
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
                16 => Aes128Gcm::new_from_slice(&key)
                    .expect("key must be valid")
                    .encrypt(Nonce::from_slice(&nonce), b"dek".as_slice())
                    .expect("encryption must succeed"),
                24 => Aes192Gcm::new_from_slice(&key)
                    .expect("key must be valid")
                    .encrypt(Nonce::from_slice(&nonce), b"dek".as_slice())
                    .expect("encryption must succeed"),
                32 => Aes256Gcm::new_from_slice(&key)
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
