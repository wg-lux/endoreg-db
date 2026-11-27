//! Admission decisions over verified evidence. Django owns locks and storage reads.
use crate::state_token::{state_tokens, Vocabulary};
use pyo3::prelude::*;
use pyo3_stub_gen::derive::gen_stub_pyfunction;

state_tokens! {
    enum ExportIntegrity {
        NotReady => "not_ready",
        MissingProcessedMedia => "missing_processed_media",
        MissingHash => "missing_hash",
        HashMetadataMismatch => "hash_metadata_mismatch",
        PersistedVerified => "persisted_verified",
        Verified => "verified",
        ProcessedMediaUnreadable => "processed_media_unreadable",
        ProcessedMediaHashMismatch => "processed_media_hash_mismatch",
    }
}

impl ExportIntegrity {
    fn blocker(self) -> &'static str {
        match self {
            ExportIntegrity::NotReady => "not ready for export",
            ExportIntegrity::MissingProcessedMedia => "processed media missing",
            ExportIntegrity::MissingHash => "processed media hash missing",
            ExportIntegrity::HashMetadataMismatch => "processed media hash metadata mismatch",
            ExportIntegrity::ProcessedMediaUnreadable => "processed media unreadable",
            ExportIntegrity::ProcessedMediaHashMismatch => "processed media hash mismatch",
            ExportIntegrity::PersistedVerified | ExportIntegrity::Verified => "",
        }
    }
}

#[gen_stub_pyfunction]
#[pyfunction]
pub(crate) fn hub_export_integrity_blocker(integrity: &str) -> PyResult<&'static str> {
    ExportIntegrity::parse(integrity)
        .map(ExportIntegrity::blocker)
        .ok_or_else(|| {
            pyo3::exceptions::PyValueError::new_err("Unknown hub export integrity status")
        })
}

pub(crate) fn vocabularies() -> Vec<Vocabulary> {
    vec![("hub_export_integrity", ExportIntegrity::tokens())]
}

#[pyfunction]
#[pyo3(signature = (present, approved, media_digest, approval_digest, verify, actual))]
pub(crate) fn hub_export_integrity(
    present: bool,
    approved: bool,
    media_digest: &str,
    approval_digest: &str,
    verify: bool,
    actual: Option<&str>,
) -> &'static str {
    use ExportIntegrity::*;
    (if !present {
        MissingProcessedMedia
    } else if !approved {
        NotReady
    } else if media_digest.is_empty() || approval_digest.is_empty() {
        MissingHash
    } else if media_digest != approval_digest {
        HashMetadataMismatch
    } else if !verify {
        PersistedVerified
    } else {
        match actual {
            None => ProcessedMediaUnreadable,
            Some(hash) if hash == media_digest => Verified,
            Some(_) => ProcessedMediaHashMismatch,
        }
    })
    .as_str()
}

#[gen_stub_pyfunction]
#[pyfunction]
pub(crate) fn media_validation_available(processing_error: bool, integrity_status: &str) -> bool {
    !(processing_error || integrity_status.eq_ignore_ascii_case("lost"))
}

#[pyfunction]
#[pyo3(signature = (active_lease, normalization_profile, source_timeline_present, quality_profile, hls_source, processed_source, segment_timeline_valid))]
pub(crate) fn raw_video_cleanup_blockers(
    active_lease: bool,
    normalization_profile: Option<&str>,
    source_timeline_present: bool,
    quality_profile: Option<&str>,
    hls_source: Option<&str>,
    processed_source: &str,
    segment_timeline_valid: bool,
) -> Vec<&'static str> {
    let mut blockers = Vec::new();
    if active_lease {
        blockers.push("active_media_operation_lease");
    }
    if normalization_profile.is_none() {
        blockers.push("storage_normalization_evidence_missing");
    }
    if !source_timeline_present {
        blockers.push("source_timeline_evidence_missing");
    }
    match (quality_profile, normalization_profile) {
        (None, _) => blockers.push("clinical_frame_quality_approval_missing"),
        (Some(q), Some(n)) if q != n => blockers.push("clinical_frame_quality_profile_mismatch"),
        _ => (),
    }
    match hls_source {
        None => blockers.push("processed_hls_not_ready"),
        Some(source) if source.is_empty() || source != processed_source => {
            blockers.push("processed_hls_generation_mismatch")
        }
        _ => (),
    }
    if source_timeline_present && !segment_timeline_valid {
        blockers.push("segment_timeline_evidence_invalid");
    }
    blockers
}

#[gen_stub_pyfunction]
#[pyfunction]
pub(crate) fn video_hub_export_blocker(
    state_present: bool,
    anonymization_ready: bool,
    segment: &str,
    present: bool,
    approved: bool,
    integrity: &str,
) -> PyResult<&'static str> {
    use crate::state_enums::SegmentAnnotationStatus as Segment;
    use crate::state_token::StateToken;
    use pyo3::exceptions::PyValueError;
    let segment = Segment::parse_token(segment)
        .ok_or_else(|| PyValueError::new_err("Unknown segment validation status"))?;
    let integrity = ExportIntegrity::parse(integrity)
        .ok_or_else(|| PyValueError::new_err("Unknown hub export integrity status"))?;
    Ok(if !state_present || !anonymization_ready {
        "not ready for export"
    } else if !present || integrity == ExportIntegrity::MissingProcessedMedia {
        "processed media missing"
    } else if matches!(segment, Segment::CleanupQueued | Segment::CleanupRunning) {
        "segment cleanup pending"
    } else if segment == Segment::CleanupFailed {
        "segment cleanup failed"
    } else if segment != Segment::Validated || !approved {
        "not ready for export"
    } else {
        integrity.blocker()
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn source_and_approval_are_required_before_verified_bytes_can_authorize_transfer() {
        for (present, approved, source, approval, actual, expected) in [
            (
                false,
                true,
                "a",
                "a",
                Some("a"),
                ExportIntegrity::MissingProcessedMedia,
            ),
            (true, false, "a", "a", Some("a"), ExportIntegrity::NotReady),
            (true, true, "", "", Some(""), ExportIntegrity::MissingHash),
            (
                true,
                true,
                "a",
                "b",
                Some("a"),
                ExportIntegrity::HashMetadataMismatch,
            ),
            (
                true,
                true,
                "a",
                "a",
                None,
                ExportIntegrity::ProcessedMediaUnreadable,
            ),
            (
                true,
                true,
                "a",
                "a",
                Some("b"),
                ExportIntegrity::ProcessedMediaHashMismatch,
            ),
            (true, true, "a", "a", Some("a"), ExportIntegrity::Verified),
        ] {
            assert_eq!(
                hub_export_integrity(present, approved, source, approval, true, actual),
                expected.as_str()
            );
        }
    }

    #[test]
    fn raw_cleanup_reports_every_missing_or_mismatched_receipt_in_order() {
        assert_eq!(
            raw_video_cleanup_blockers(true, None, false, None, None, "", false),
            [
                "active_media_operation_lease",
                "storage_normalization_evidence_missing",
                "source_timeline_evidence_missing",
                "clinical_frame_quality_approval_missing",
                "processed_hls_not_ready"
            ]
        );
        assert_eq!(
            raw_video_cleanup_blockers(
                false,
                Some("p"),
                true,
                Some("q"),
                Some("old"),
                "new",
                false
            ),
            [
                "clinical_frame_quality_profile_mismatch",
                "processed_hls_generation_mismatch",
                "segment_timeline_evidence_invalid"
            ]
        );
        assert!(raw_video_cleanup_blockers(
            false,
            Some("p"),
            true,
            Some("p"),
            Some("master"),
            "master",
            true
        )
        .is_empty());
    }
}

// Only schema field names and their cleared representations cross back into Python.
const IDENTIFIER_FIELDS: &[(&str, Option<&str>)] = &[
    ("patient_first_name", None),
    ("patient_last_name", None),
    ("patient_dob", None),
    ("examination_date", None),
    ("examination_time", None),
    ("casenumber", None),
    ("examiner_first_name", None),
    ("examiner_last_name", None),
    ("file_path", None),
    ("text", None),
    ("anonymized_text", None),
    ("endoscope_sn", None),
    ("external_id", None),
    ("validation_comment", Some("")),
];

#[gen_stub_pyfunction]
#[pyfunction]
pub(crate) fn direct_identifier_fields() -> Vec<&'static str> {
    IDENTIFIER_FIELDS.iter().map(|(field, _)| *field).collect()
}

fn identifier_present(value: &Bound<'_, PyAny>) -> PyResult<bool> {
    use pyo3::types::PyString;
    if value.is_none() {
        return Ok(false);
    }
    if !value.is_instance_of::<PyString>() {
        return Ok(true);
    }
    let trimmed = value.call_method0("strip")?;
    // All placeholder tokens are at most nine characters. Avoid case-folding a
    // potentially long clinical note to decide that its field needs clearing.
    match trimmed.len()? {
        0 => Ok(false),
        1..=9 => {
            let folded = trimmed.call_method0("casefold")?;
            for placeholder in ["unknown", "none", "null", "undefined"] {
                if folded.eq(placeholder)? {
                    return Ok(false);
                }
            }
            Ok(true)
        }
        _ => Ok(true),
    }
}

#[pyfunction]
pub(crate) fn direct_identifier_clear_values(
    record: &Bound<'_, PyAny>,
) -> PyResult<Vec<(&'static str, Option<&'static str>)>> {
    let mut values = Vec::new();
    for &(field, cleared) in IDENTIFIER_FIELDS {
        if identifier_present(&record.getattr(field)?)? {
            values.push((field, cleared));
        }
    }
    Ok(values)
}
