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

#[derive(Clone, Copy)]
enum Flag {
    ProcessingError,
    AnonymizationValidated,
    SensitiveMetaProcessed,
    FramesExtracted,
    Anonymized,
    WasCreated,
    ProcessingStarted,
}

use Flag::*;
type StatusRule = (AnonymizationStatus, &'static [(Flag, bool)]);
const FLAG_NAMES: [&str; 7] = [
    "processing_error",
    "anonymization_validated",
    "sensitive_meta_processed",
    "frames_extracted",
    "anonymized",
    "was_created",
    "processing_started",
];
const COMMON_RULES: &[StatusRule] = &[
    (AnonymizationStatus::Failed, &[(ProcessingError, true)]),
    (
        AnonymizationStatus::Validated,
        &[(AnonymizationValidated, true)],
    ),
    (
        AnonymizationStatus::DoneProcessingAnonymization,
        &[(SensitiveMetaProcessed, true)],
    ),
];
const VIDEO_RULES: &[StatusRule] = &[
    (
        AnonymizationStatus::ProcessingAnonymization,
        &[(FramesExtracted, true), (Anonymized, false)],
    ),
    (
        AnonymizationStatus::ExtractingFrames,
        &[(WasCreated, true), (FramesExtracted, false)],
    ),
];
const REPORT_RULES: &[StatusRule] = &[(
    AnonymizationStatus::ProcessingAnonymization,
    &[(ProcessingStarted, true), (Anonymized, false)],
)];
const FINAL_RULES: &[StatusRule] = &[
    (AnonymizationStatus::Started, &[(ProcessingStarted, true)]),
    (AnonymizationStatus::Anonymized, &[(Anonymized, true)]),
];

fn rules(report: bool) -> impl Iterator<Item = &'static StatusRule> {
    COMMON_RULES
        .iter()
        .chain(if report { REPORT_RULES } else { VIDEO_RULES })
        .chain(FINAL_RULES)
}

fn resolve_status(state: AnonymizationStateFlags, report: bool) -> AnonymizationStatus {
    rules(report)
        .find(|(_, conditions)| {
            conditions.iter().all(|&(flag, value)| {
                let actual = match flag {
                    ProcessingError => state.processing_error,
                    AnonymizationValidated => state.anonymization_validated,
                    SensitiveMetaProcessed => state.sensitive_meta_processed,
                    FramesExtracted => state.frames_extracted,
                    Anonymized => state.anonymized,
                    WasCreated => state.was_created,
                    ProcessingStarted => state.processing_started,
                };
                actual == value
            })
        })
        .map_or(AnonymizationStatus::NotStarted, |&(status, _)| status)
}

#[pyfunction]
pub(crate) fn anonymization_status_rules(
    report: bool,
) -> Vec<(&'static str, Vec<(&'static str, bool)>)> {
    rules(report)
        .map(|(status, conditions)| {
            (
                status.as_str(),
                conditions
                    .iter()
                    .map(|&(flag, value)| (FLAG_NAMES[flag as usize], value))
                    .collect(),
            )
        })
        .collect()
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
    resolve_status(state, false).as_str()
}

#[pyfunction]
pub(crate) fn derive_report_anonymization_status(
    processing_error: bool,
    anonymization_validated: bool,
    sensitive_meta_processed: bool,
    anonymized: bool,
    processing_started: bool,
) -> &'static str {
    let state = AnonymizationStateFlags {
        frames_extracted: false,
        was_created: false,
        processing_error,
        anonymization_validated,
        sensitive_meta_processed,
        anonymized,
        processing_started,
    };
    resolve_status(state, true).as_str()
}

#[cfg(test)]
mod tests {
    use super::{resolve_status, AnonymizationStateFlags, AnonymizationStatus};

    const EMPTY_STATE: AnonymizationStateFlags = AnonymizationStateFlags {
        processing_error: false,
        anonymization_validated: false,
        sensitive_meta_processed: false,
        frames_extracted: false,
        anonymized: false,
        was_created: false,
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
            assert_eq!(resolve_status(state, false), expected_status);
        }
    }

    #[test]
    fn resolves_report_anonymization_status_from_immutable_state_flags() {
        let cases = [
            (
                AnonymizationStateFlags {
                    anonymization_validated: true,
                    processing_error: true,
                    ..EMPTY_STATE
                },
                AnonymizationStatus::Failed,
            ),
            (
                AnonymizationStateFlags {
                    sensitive_meta_processed: true,
                    processing_error: true,
                    ..EMPTY_STATE
                },
                AnonymizationStatus::Failed,
            ),
            (
                AnonymizationStateFlags {
                    processing_started: true,
                    anonymized: false,
                    ..EMPTY_STATE
                },
                AnonymizationStatus::ProcessingAnonymization,
            ),
            (
                AnonymizationStateFlags {
                    processing_started: true,
                    processing_error: true,
                    anonymized: false,
                    ..EMPTY_STATE
                },
                AnonymizationStatus::Failed,
            ),
            (
                AnonymizationStateFlags {
                    processing_started: true,
                    anonymized: true,
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
            assert_eq!(resolve_status(state, true), expected_status);
        }
    }
}
