//! Pure decisions for the privacy, annotation and release tracks of a clinical record.
use crate::state_token::{state_tokens, StateToken, Vocabulary};
use pyo3::{exceptions::PyValueError, prelude::*};

state_tokens! {
    enum Privacy {
        ImportPending => "import_pending",
        Importing => "importing",
        Quarantined => "quarantined",
        AwaitingValidation => "awaiting_validation",
        Validated => "validated",
        RawPurged => "raw_purged",
        Failed => "failed",
        Lost => "lost",
        Withdrawn => "withdrawn",
    }
}

state_tokens! {
    enum Annotation {
        NotAvailable => "not_available",
        InProgress => "in_progress",
        Validated => "validated",
    }
}

state_tokens! {
    enum Release {
        NotReleased => "not_released",
        ExportReady => "export_ready",
        Exported => "exported",
        DatasetIngested => "dataset_ingested",
    }
}

state_tokens! {
    enum Event {
        ImportStarted => "import_started",
        Quarantined => "quarantined",
        QuarantineReleased => "quarantine_released",
        ArtifactPublished => "artifact_published",
        CorrectionRequested => "correction_requested",
        AnonymizationValidated => "anonymization_validated",
        RawPurged => "raw_purged",
        ProcessingFailed => "processing_failed",
        IntegrityLost => "integrity_lost",
        RetryRequested => "retry_requested",
        ReconcileRetry => "reconcile_retry",
        ReconcileFail => "reconcile_fail",
        ConsentWithdrawn => "consent_withdrawn",
        AnnotationCompleted => "annotation_completed",
        AnnotationReopened => "annotation_reopened",
        ExportApproved => "export_approved",
        ExportRevoked => "export_revoked",
        Exported => "exported",
        DatasetIngested => "dataset_ingested",
    }
}

state_tokens! {
    enum Approval {
        None => "none",
        Current => "current",
        Stale => "stale",
    }
}

state_tokens! {
    enum Rejection {
        EventNotPermittedInState => "event_not_permitted_in_state",
        RecordInactive => "record_inactive",
        ValidationIsFinal => "validation_is_final",
        IdentityNotVerified => "identity_not_verified",
        PrivacyTrackIncomplete => "privacy_track_incomplete",
        AnnotationTrackIncomplete => "annotation_track_incomplete",
        AlreadyReleased => "already_released",
    }
}

state_tokens! {
    enum Violation {
        AnonymizedWithoutArtifact => "anonymized_without_artifact",
        ValidatedWithoutArtifact => "validated_without_artifact",
        ValidatedWithoutVerifiedIdentity => "validated_without_verified_identity",
        IdentifiersErasedBeforeVerification => "identifiers_erased_before_verification",
        LegacyExemptionWithoutValidation => "legacy_exemption_without_validation",
        ReleasedWithoutAnonymizationValidation => "released_without_anonymization_validation",
        ReleasedBeforeRawPurge => "released_before_raw_purge",
        ReleasedWithoutAnnotationValidation => "released_without_annotation_validation",
    }
}

pub(crate) fn vocabularies() -> Vec<Vocabulary> {
    vec![
        ("clinical_privacy", Privacy::tokens()),
        ("clinical_annotation", Annotation::tokens()),
        ("clinical_release", Release::tokens()),
        ("clinical_event", Event::tokens()),
        ("clinical_approval", Approval::tokens()),
        ("clinical_rejection", Rejection::tokens()),
        ("clinical_violation", Violation::tokens()),
    ]
}

fn parse<T: StateToken>(value: &str) -> PyResult<T> {
    T::parse_token(value)
        .ok_or_else(|| PyValueError::new_err(format!("Unknown clinical workflow token: {value}")))
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
struct State {
    privacy: Privacy,
    annotation: Annotation,
    release: Release,
}

impl State {
    fn inactive(self) -> bool {
        matches!(
            self.privacy,
            Privacy::Failed | Privacy::Lost | Privacy::Withdrawn
        )
    }

    fn released(self) -> bool {
        matches!(self.release, Release::Exported | Release::DatasetIngested)
    }

    fn tokens(self) -> (&'static str, &'static str, &'static str) {
        (
            self.privacy.as_str(),
            self.annotation.as_str(),
            self.release.as_str(),
        )
    }

    fn reduce(mut self, event: Event) -> Result<Self, Rejection> {
        use Annotation as A;
        use Event as E;
        use Privacy as P;
        use Rejection as Error;
        use Release as R;
        match event {
            E::ConsentWithdrawn => {
                self.privacy = P::Withdrawn;
                self.release = R::NotReleased;
            }
            E::IntegrityLost if self.privacy != P::Withdrawn => self.privacy = P::Lost,
            E::RetryRequested if self.privacy == P::Failed => self.privacy = P::ImportPending,
            E::ReconcileRetry if self.privacy == P::Lost => self.privacy = P::ImportPending,
            E::ReconcileFail if self.privacy == P::Lost => self.privacy = P::Failed,
            E::ProcessingFailed if !matches!(self.privacy, P::Lost | P::Withdrawn) => {
                self.privacy = P::Failed
            }
            _ if self.inactive() => return Err(Error::RecordInactive),
            E::ImportStarted if matches!(self.privacy, P::ImportPending | P::Importing) => {
                self.privacy = P::Importing
            }
            E::Quarantined if matches!(self.privacy, P::Importing | P::Quarantined) => {
                self.privacy = P::Quarantined
            }
            E::QuarantineReleased if self.privacy == P::Quarantined => self.privacy = P::Importing,
            E::ArtifactPublished
                if matches!(self.privacy, P::Importing | P::AwaitingValidation) =>
            {
                self.privacy = P::AwaitingValidation;
                if self.annotation == A::NotAvailable {
                    self.annotation = A::InProgress;
                }
            }
            E::CorrectionRequested if self.privacy == P::AwaitingValidation => {
                self.privacy = P::Importing
            }
            E::CorrectionRequested if matches!(self.privacy, P::Validated | P::RawPurged) => {
                return Err(Error::ValidationIsFinal)
            }
            E::AnonymizationValidated
                if matches!(self.privacy, P::AwaitingValidation | P::Validated) =>
            {
                self.privacy = P::Validated
            }
            E::RawPurged if matches!(self.privacy, P::Validated | P::RawPurged) => {
                self.privacy = P::RawPurged
            }
            E::AnnotationCompleted if self.annotation != A::NotAvailable => {
                self.annotation = A::Validated
            }
            E::AnnotationReopened if self.released() => return Err(Error::AlreadyReleased),
            E::AnnotationReopened if self.annotation != A::NotAvailable => {
                self.annotation = A::InProgress;
                if self.release == R::ExportReady {
                    self.release = R::NotReleased;
                }
            }
            E::ExportApproved if self.privacy != P::RawPurged => {
                return Err(Error::PrivacyTrackIncomplete)
            }
            E::ExportApproved if self.annotation != A::Validated => {
                return Err(Error::AnnotationTrackIncomplete)
            }
            E::ExportApproved if !self.released() => self.release = R::ExportReady,
            E::ExportRevoked if !self.released() => self.release = R::NotReleased,
            E::Exported if matches!(self.release, R::ExportReady | R::Exported) => {
                self.release = R::Exported
            }
            E::DatasetIngested if self.release != R::NotReleased => {
                self.release = R::DatasetIngested
            }
            _ if self.released() => return Err(Error::AlreadyReleased),
            _ => return Err(Error::EventNotPermittedInState),
        }
        Ok(self)
    }
}

#[pyfunction]
pub(crate) fn reduce_clinical_workflow(
    privacy: &str,
    annotation: &str,
    release: &str,
    event: &str,
) -> PyResult<(&'static str, &'static str, &'static str)> {
    let state = State {
        privacy: parse(privacy)?,
        annotation: parse(annotation)?,
        release: parse(release)?,
    };
    state
        .reduce(parse(event)?)
        .map(State::tokens)
        .map_err(|reason| PyValueError::new_err(reason.as_str()))
}

#[pyfunction]
pub(crate) fn clinical_evidence_rejection(
    event: &str,
    identity_verified: bool,
) -> PyResult<Option<&'static str>> {
    Ok(match parse(event)? {
        Event::AnonymizationValidated if !identity_verified => {
            Some(Rejection::IdentityNotVerified.as_str())
        }
        _ => None,
    })
}

#[derive(FromPyObject)]
pub(crate) struct Facts {
    integrity_lost: bool,
    processing_failed: bool,
    processing_started: bool,
    anonymized_flag: bool,
    processed_artifact_present: bool,
    anonymization_validated: bool,
    identity_verified: bool,
    legacy_identity_exemption: bool,
    raw_source_present: bool,
    direct_identifiers_erased: bool,
    medical_annotation_complete: bool,
    export_approval: String,
    dataset_member: bool,
}

impl Facts {
    fn project(&self, approval: Approval) -> (State, Vec<&'static str>) {
        use Violation as V;
        let published = self.anonymized_flag && self.processed_artifact_present;
        let attested = self.identity_verified || self.legacy_identity_exemption;
        let purged = !self.raw_source_present && self.direct_identifiers_erased;
        let release = if self.dataset_member {
            Release::DatasetIngested
        } else if approval == Approval::Current {
            Release::ExportReady
        } else {
            Release::NotReleased
        };
        let privacy = if self.integrity_lost {
            Privacy::Lost
        } else if self.processing_failed {
            Privacy::Failed
        } else if !published && self.processing_started {
            Privacy::Importing
        } else if !published {
            Privacy::ImportPending
        } else if !self.anonymization_validated {
            Privacy::AwaitingValidation
        } else if purged {
            Privacy::RawPurged
        } else {
            Privacy::Validated
        };
        let annotation = if !published {
            Annotation::NotAvailable
        } else if self.medical_annotation_complete {
            Annotation::Validated
        } else {
            Annotation::InProgress
        };
        let mut violations = Vec::new();
        let mut check = |invalid, reason: V| {
            if invalid {
                violations.push(reason.as_str());
            }
        };
        check(
            self.legacy_identity_exemption && !self.anonymization_validated,
            V::LegacyExemptionWithoutValidation,
        );
        check(
            self.direct_identifiers_erased && !attested,
            V::IdentifiersErasedBeforeVerification,
        );
        if !matches!(privacy, Privacy::Lost | Privacy::Failed) {
            check(
                self.anonymized_flag && !published,
                V::AnonymizedWithoutArtifact,
            );
            check(
                self.anonymization_validated && !published,
                V::ValidatedWithoutArtifact,
            );
            check(
                self.anonymization_validated && !attested,
                V::ValidatedWithoutVerifiedIdentity,
            );
            if release != Release::NotReleased {
                check(
                    !self.anonymization_validated,
                    V::ReleasedWithoutAnonymizationValidation,
                );
                check(!purged, V::ReleasedBeforeRawPurge);
                check(
                    !self.medical_annotation_complete,
                    V::ReleasedWithoutAnnotationValidation,
                );
            }
        }
        (
            State {
                privacy,
                annotation,
                release,
            },
            violations,
        )
    }
}

type Projection = (
    (&'static str, &'static str, &'static str),
    Vec<&'static str>,
);

#[pyfunction]
pub(crate) fn project_clinical_workflow(facts: Facts) -> PyResult<Projection> {
    let (state, violations) = facts.project(parse(&facts.export_approval)?);
    Ok((state.tokens(), violations))
}

// Enumerate single-track edges with the other tracks satisfying admission.
// Cross-track effects and rejection reasons are returned by the reducer itself.
type Edge = (&'static str, &'static str, &'static str);
type TrackTables = (Vec<Edge>, Vec<Edge>, Vec<Edge>);

#[pyfunction]
pub(crate) fn clinical_workflow_tables() -> TrackTables {
    let base = State {
        privacy: Privacy::RawPurged,
        annotation: Annotation::Validated,
        release: Release::NotReleased,
    };
    let (mut privacy, mut annotation, mut release) = (Vec::new(), Vec::new(), Vec::new());
    use Event::*;
    for &event in Event::ALL {
        match event {
            ImportStarted
            | Quarantined
            | QuarantineReleased
            | ArtifactPublished
            | CorrectionRequested
            | AnonymizationValidated
            | RawPurged
            | ProcessingFailed
            | IntegrityLost
            | RetryRequested
            | ReconcileRetry
            | ReconcileFail
            | ConsentWithdrawn => {
                for &source in Privacy::ALL {
                    if let Ok(next) = (State {
                        privacy: source,
                        ..base
                    })
                    .reduce(event)
                    {
                        privacy.push((event.as_str(), source.as_str(), next.privacy.as_str()));
                    }
                }
            }
            AnnotationCompleted | AnnotationReopened => {
                for &source in Annotation::ALL {
                    if let Ok(next) = (State {
                        annotation: source,
                        ..base
                    })
                    .reduce(event)
                    {
                        annotation.push((
                            event.as_str(),
                            source.as_str(),
                            next.annotation.as_str(),
                        ));
                    }
                }
            }
            ExportApproved | ExportRevoked | Exported | DatasetIngested => {
                for &source in Release::ALL {
                    if let Ok(next) = (State {
                        release: source,
                        ..base
                    })
                    .reduce(event)
                    {
                        release.push((event.as_str(), source.as_str(), next.release.as_str()));
                    }
                }
            }
        }
    }
    (privacy, annotation, release)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn every_event_has_an_edge_and_withdrawal_is_absorbing() {
        let (privacy, annotations, releases) = clinical_workflow_tables();
        for &event in Event::ALL {
            assert!(
                privacy
                    .iter()
                    .chain(&annotations)
                    .chain(&releases)
                    .any(|&(token, _, _)| token == event.as_str()),
                "{event:?}"
            );
            for &annotation in Annotation::ALL {
                for &release in Release::ALL {
                    let withdrawn = State {
                        privacy: Privacy::Withdrawn,
                        annotation,
                        release,
                    };
                    if let Ok(target) = withdrawn.reduce(event) {
                        assert_eq!(event, Event::ConsentWithdrawn);
                        assert_eq!(target.privacy, Privacy::Withdrawn);
                        assert_eq!(target.release, Release::NotReleased);
                    }
                }
            }
        }
    }
}
