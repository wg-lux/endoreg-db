use pyo3::prelude::*;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum AnonymizationStatus {
    NotStarted,
    ExtractingFrames,
    ProcessingAnonymization,
    DoneProcessingAnonymization,
    Validated,
    Failed,
    Started,
    Anonymized,
}

impl AnonymizationStatus {
    const fn as_str(self) -> &'static str {
        match self {
            Self::NotStarted => "not_started",
            Self::ExtractingFrames => "extracting_frames",
            Self::ProcessingAnonymization => "processing_anonymization",
            Self::DoneProcessingAnonymization => "done_processing_anonymization",
            Self::Validated => "validated",
            Self::Failed => "failed",
            Self::Started => "started",
            Self::Anonymized => "anonymized",
        }
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
struct AnonymizationStateFlags {
    processing_error: bool,
    anonymization_validated: bool,
    sensitive_meta_processed: bool,
    frames_extracted: bool,
    anonymized: bool,
    was_created: bool,
    processing_started: bool,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
struct ReportAnonymizationStateFlags {
    processing_error: bool,
    anonymization_validated: bool,
    sensitive_meta_processed: bool,
    anonymized: bool,
    processing_started: bool,
}

fn resolve_anonymization_status(state: AnonymizationStateFlags) -> AnonymizationStatus {
    match state {
        AnonymizationStateFlags {
            processing_error: true,
            ..
        } => AnonymizationStatus::Failed,
        AnonymizationStateFlags {
            anonymization_validated: true,
            ..
        } => AnonymizationStatus::Validated,
        AnonymizationStateFlags {
            sensitive_meta_processed: true,
            ..
        } => AnonymizationStatus::DoneProcessingAnonymization,
        AnonymizationStateFlags {
            frames_extracted: true,
            anonymized: false,
            ..
        } => AnonymizationStatus::ProcessingAnonymization,
        AnonymizationStateFlags {
            was_created: true,
            frames_extracted: false,
            ..
        } => AnonymizationStatus::ExtractingFrames,
        AnonymizationStateFlags {
            processing_started: true,
            ..
        } => AnonymizationStatus::Started,
        AnonymizationStateFlags {
            anonymized: true, ..
        } => AnonymizationStatus::Anonymized,
        _ => AnonymizationStatus::NotStarted,
    }
}

fn resolve_report_anonymization_status(
    state: ReportAnonymizationStateFlags,
) -> AnonymizationStatus {
    match state {
        ReportAnonymizationStateFlags {
            processing_error: true,
            ..
        } => AnonymizationStatus::Failed,
        ReportAnonymizationStateFlags {
            anonymization_validated: true,
            ..
        } => AnonymizationStatus::Validated,
        ReportAnonymizationStateFlags {
            sensitive_meta_processed: true,
            ..
        } => AnonymizationStatus::DoneProcessingAnonymization,
        ReportAnonymizationStateFlags {
            processing_started: true,
            processing_error: false,
            anonymized: false,
            ..
        } => AnonymizationStatus::ProcessingAnonymization,
        ReportAnonymizationStateFlags {
            processing_started: true,
            ..
        } => AnonymizationStatus::Started,
        ReportAnonymizationStateFlags {
            anonymized: true, ..
        } => AnonymizationStatus::Anonymized,
        _ => AnonymizationStatus::NotStarted,
    }
}

#[pyfunction]
pub(crate) fn derive_anonymization_status(
    processing_error: bool,
    anonymization_validated: bool,
    sensitive_meta_processed: bool,
    frames_extracted: bool,
    anonymized: bool,
    was_created: bool,
    processing_started: bool,
) -> &'static str {
    let state = AnonymizationStateFlags {
        processing_error,
        anonymization_validated,
        sensitive_meta_processed,
        frames_extracted,
        anonymized,
        was_created,
        processing_started,
    };
    resolve_anonymization_status(state).as_str()
}

#[pyfunction]
pub(crate) fn derive_report_anonymization_status(
    processing_error: bool,
    anonymization_validated: bool,
    sensitive_meta_processed: bool,
    anonymized: bool,
    processing_started: bool,
) -> &'static str {
    let state = ReportAnonymizationStateFlags {
        processing_error,
        anonymization_validated,
        sensitive_meta_processed,
        anonymized,
        processing_started,
    };
    resolve_report_anonymization_status(state).as_str()
}

#[cfg(test)]
mod tests {
    use super::{
        resolve_anonymization_status, resolve_report_anonymization_status, AnonymizationStateFlags,
        AnonymizationStatus, ReportAnonymizationStateFlags,
    };

    const EMPTY_STATE: AnonymizationStateFlags = AnonymizationStateFlags {
        processing_error: false,
        anonymization_validated: false,
        sensitive_meta_processed: false,
        frames_extracted: false,
        anonymized: false,
        was_created: false,
        processing_started: false,
    };

    const EMPTY_REPORT_STATE: ReportAnonymizationStateFlags = ReportAnonymizationStateFlags {
        processing_error: false,
        anonymization_validated: false,
        sensitive_meta_processed: false,
        anonymized: false,
        processing_started: false,
    };

    #[test]
    fn resolves_anonymization_status_from_immutable_state_flags() {
        let cases = [
            (
                AnonymizationStateFlags {
                    processing_error: true,
                    anonymization_validated: true,
                    ..EMPTY_STATE
                },
                AnonymizationStatus::Failed,
            ),
            (
                AnonymizationStateFlags {
                    anonymization_validated: true,
                    sensitive_meta_processed: true,
                    ..EMPTY_STATE
                },
                AnonymizationStatus::Validated,
            ),
            (
                AnonymizationStateFlags {
                    sensitive_meta_processed: true,
                    frames_extracted: true,
                    ..EMPTY_STATE
                },
                AnonymizationStatus::DoneProcessingAnonymization,
            ),
            (
                AnonymizationStateFlags {
                    frames_extracted: true,
                    anonymized: false,
                    ..EMPTY_STATE
                },
                AnonymizationStatus::ProcessingAnonymization,
            ),
            (
                AnonymizationStateFlags {
                    was_created: true,
                    frames_extracted: false,
                    ..EMPTY_STATE
                },
                AnonymizationStatus::ExtractingFrames,
            ),
            (
                AnonymizationStateFlags {
                    processing_started: true,
                    ..EMPTY_STATE
                },
                AnonymizationStatus::Started,
            ),
            (
                AnonymizationStateFlags {
                    anonymized: true,
                    ..EMPTY_STATE
                },
                AnonymizationStatus::Anonymized,
            ),
            (EMPTY_STATE, AnonymizationStatus::NotStarted),
        ];

        for (state, expected_status) in cases {
            assert_eq!(resolve_anonymization_status(state), expected_status);
        }
    }

    #[test]
    fn resolves_report_anonymization_status_from_immutable_state_flags() {
        let cases = [
            (
                ReportAnonymizationStateFlags {
                    anonymization_validated: true,
                    processing_error: true,
                    ..EMPTY_REPORT_STATE
                },
                AnonymizationStatus::Failed,
            ),
            (
                ReportAnonymizationStateFlags {
                    sensitive_meta_processed: true,
                    processing_error: true,
                    ..EMPTY_REPORT_STATE
                },
                AnonymizationStatus::Failed,
            ),
            (
                ReportAnonymizationStateFlags {
                    processing_started: true,
                    anonymized: false,
                    ..EMPTY_REPORT_STATE
                },
                AnonymizationStatus::ProcessingAnonymization,
            ),
            (
                ReportAnonymizationStateFlags {
                    processing_started: true,
                    processing_error: true,
                    anonymized: false,
                    ..EMPTY_REPORT_STATE
                },
                AnonymizationStatus::Failed,
            ),
            (
                ReportAnonymizationStateFlags {
                    processing_started: true,
                    anonymized: true,
                    ..EMPTY_REPORT_STATE
                },
                AnonymizationStatus::Started,
            ),
            (
                ReportAnonymizationStateFlags {
                    anonymized: true,
                    ..EMPTY_REPORT_STATE
                },
                AnonymizationStatus::Anonymized,
            ),
            (EMPTY_REPORT_STATE, AnonymizationStatus::NotStarted),
        ];

        for (state, expected_status) in cases {
            assert_eq!(resolve_report_anonymization_status(state), expected_status);
        }
    }
}
