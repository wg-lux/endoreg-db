//! Status transition tables for persisted lifecycle fields.
//!
//! Each machine is one table from (current, target) to Apply, Ignore or an
//! error. `None` as current is an unsaved row. Python writers in
//! `endoreg_db.services.runtime.status_writers` lock the row, read the
//! persisted status and ask `decide_status_transition` before assigning.

use crate::state_token::{state_tokens, StateToken, Vocabulary};
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum Decision {
    /// Persist the target status.
    Apply,
    /// The row already reached or passed the target; write nothing.
    Ignore,
}

/// `None` marks an illegal transition.
type Verdict = Option<Decision>;

use Decision::{Apply, Ignore};

state_tokens! {
    enum Machine {
        ReportLlmInferenceJob => "report_llm_inference_job",
        VideoProcessingHistory => "video_processing_history",
        QuarantineItem => "quarantine_item",
        FrameAnnotationExportJob => "frame_annotation_export_job",
        AiDatasetExportArtifact => "ai_dataset_export_artifact",
        DicomExportJob => "dicom_export_job",
        PatientExaminationReport => "patient_examination_report",
        HubInboundTransfer => "hub_inbound_transfer",
        HubInboundCaseResolution => "hub_inbound_case_resolution",
        UploadJobSourceCleanup => "upload_job_source_cleanup",
        HubOutboundLocalCleanup => "hub_outbound_local_cleanup",
    }
}

state_tokens! {
    enum ReportLlm {
        Queued => "queued",
        Running => "running",
        Success => "success",
        Failure => "failure",
        Lost => "lost",
        Cancelled => "cancelled",
    }
}

fn report_llm(current: Option<ReportLlm>, target: ReportLlm) -> Verdict {
    use ReportLlm::*;
    match (current, target) {
        (None, Queued) => Some(Apply),
        // Broker redelivery restarts a failed or lost attempt of the same job.
        (Some(Queued | Failure | Lost), Running) => Some(Apply),
        (Some(Queued), Queued) | (Some(Running), Running) => Some(Ignore),
        (Some(Queued | Running), Success | Failure | Lost | Cancelled) => Some(Apply),
        (Some(Success | Cancelled), Failure | Lost) => Some(Ignore),
        (Some(Success), Success) | (Some(Failure), Failure) | (Some(Lost), Lost) => Some(Ignore),
        (Some(Cancelled), Cancelled) => Some(Ignore),
        _ => None,
    }
}

state_tokens! {
    enum ProcessingHistory {
        Pending => "pending",
        Running => "running",
        Success => "success",
        Failure => "failure",
        Cancelled => "cancelled",
    }
}

fn processing_history(current: Option<ProcessingHistory>, target: ProcessingHistory) -> Verdict {
    use ProcessingHistory::*;
    match (current, target) {
        (None, Pending | Running) => Some(Apply),
        (Some(Pending), Running) => Some(Apply),
        (Some(Pending), Pending) | (Some(Running), Running) => Some(Ignore),
        (Some(Pending | Running), Success | Failure | Cancelled) => Some(Apply),
        // Finished operations keep their outcome; late reports are stale.
        (Some(Success | Failure | Cancelled), Success | Failure | Cancelled) => Some(Ignore),
        _ => None,
    }
}

state_tokens! {
    enum Quarantine {
        PendingReview => "pending_review",
        Retained => "retained",
        ApprovedForDeletion => "approved_for_deletion",
        Deleted => "deleted",
        Missing => "missing",
        Failed => "failed",
    }
}

fn quarantine(current: Option<Quarantine>, target: Quarantine) -> Verdict {
    use Quarantine::*;
    match (current, target) {
        (None, PendingReview) => Some(Apply),
        // A file reappearing on disk needs a new review.
        (Some(Deleted | Missing), PendingReview) => Some(Apply),
        (Some(PendingReview), PendingReview) => Some(Ignore),
        // A review decision may be repeated or revised until deletion.
        (Some(PendingReview | Retained | ApprovedForDeletion | Failed), ApprovedForDeletion) => {
            Some(Apply)
        }
        (Some(PendingReview | Retained | ApprovedForDeletion | Failed | Missing), Retained) => {
            Some(Apply)
        }
        (Some(PendingReview | Retained | ApprovedForDeletion | Failed), Missing) => Some(Apply),
        (Some(Missing), Missing) => Some(Ignore),
        (Some(ApprovedForDeletion), Deleted | Failed) => Some(Apply),
        (Some(Deleted), Deleted) | (Some(Failed), Failed) => Some(Ignore),
        _ => None,
    }
}

state_tokens! {
    enum FrameExport {
        Queued => "queued",
        Running => "running",
        Completed => "completed",
        Failed => "failed",
        Lost => "lost",
    }
}

fn frame_export(current: Option<FrameExport>, target: FrameExport) -> Verdict {
    use FrameExport::*;
    match (current, target) {
        (None, Queued) => Some(Apply),
        (Some(Queued), Running | Failed | Lost) => Some(Apply),
        (Some(Queued), Queued) | (Some(Running), Running) => Some(Ignore),
        (Some(Running), Completed | Failed | Lost) => Some(Apply),
        (Some(Completed | Failed | Lost), Completed | Failed | Lost) => Some(Ignore),
        _ => None,
    }
}

state_tokens! {
    enum DatasetExport {
        Running => "running",
        Completed => "completed",
        Failed => "failed",
    }
}

fn dataset_export(current: Option<DatasetExport>, target: DatasetExport) -> Verdict {
    use DatasetExport::*;
    match (current, target) {
        (None, Running) => Some(Apply),
        (Some(Running), Completed | Failed) => Some(Apply),
        (Some(Running), Running) => Some(Ignore),
        // A completed artifact whose file vanished or left the export root fails.
        (Some(Completed), Failed) => Some(Apply),
        (Some(Completed), Completed) | (Some(Failed), Failed) => Some(Ignore),
        _ => None,
    }
}

state_tokens! {
    enum DicomExport {
        Received => "received",
        Imported => "imported",
        Failed => "failed",
    }
}

fn dicom_export(current: Option<DicomExport>, target: DicomExport) -> Verdict {
    use DicomExport::*;
    match (current, target) {
        (None, Received) => Some(Apply),
        (Some(Received), Imported | Failed) => Some(Apply),
        (Some(Received), Received) => Some(Ignore),
        (Some(Imported | Failed), Imported | Failed) => Some(Ignore),
        _ => None,
    }
}

state_tokens! {
    enum ExaminationReport {
        Draft => "draft",
        Final => "final",
    }
}

fn examination_report(current: Option<ExaminationReport>, target: ExaminationReport) -> Verdict {
    use ExaminationReport::*;
    match (current, target) {
        (None, Draft | Final) => Some(Apply),
        (Some(Draft), Final) => Some(Apply),
        // Reopening a final report for correction is an explicit product decision.
        (Some(Final), Draft) => Some(Apply),
        (Some(Draft), Draft) | (Some(Final), Final) => Some(Ignore),
    }
}

state_tokens! {
    enum InboundTransfer {
        Pending => "pending",
        Claimed => "claimed",
        Running => "running",
        RetryWait => "retry_wait",
        AwaitingMedia => "awaiting_media",
        Applied => "applied",
        Failed => "failed",
        Inconsistent => "inconsistent",
        Lost => "lost",
    }
}

fn inbound_transfer(current: Option<InboundTransfer>, target: InboundTransfer) -> Verdict {
    use InboundTransfer::*;
    match (current, target) {
        (None, Pending | AwaitingMedia) => Some(Apply),
        // Claims: initial, retry, reconcile, or reclaim of an expired owner.
        (Some(Pending | AwaitingMedia | Claimed | RetryWait | Failed | Lost | Running), Running) => {
            Some(Apply)
        }
        (Some(Pending | AwaitingMedia | Claimed | Running), Applied | Failed | AwaitingMedia) => {
            Some(Apply)
        }
        // An integrity or ownership conflict outranks every other outcome.
        (Some(Pending | AwaitingMedia | Claimed | Running | RetryWait | Applied | Failed | Lost),
            Inconsistent) => Some(Apply),
        (Some(Applied), Lost) => Some(Apply),
        (Some(Lost), Failed) => Some(Apply),
        (Some(Pending), Pending)
        | (Some(Applied), Applied)
        | (Some(Failed), Failed)
        | (Some(Lost), Lost) => Some(Ignore),
        (Some(Inconsistent), Inconsistent) => Some(Ignore),
        _ => None,
    }
}

state_tokens! {
    enum CaseResolution {
        Pending => "pending",
        Linked => "linked",
        Ambiguous => "ambiguous",
        Unresolved => "unresolved",
    }
}

fn case_resolution(current: Option<CaseResolution>, target: CaseResolution) -> Verdict {
    use CaseResolution::*;
    match (current, target) {
        (None, _) => Some(Apply),
        (Some(previous), next) if previous == next => Some(Ignore),
        // Each applied delivery resolves the case again from current evidence.
        (Some(_), Linked | Ambiguous | Unresolved) => Some(Apply),
        (Some(_), Pending) => None,
    }
}

state_tokens! {
    enum SourceCleanup {
        Pending => "pending",
        Eligible => "eligible",
        Deleting => "deleting",
        Completed => "completed",
        Skipped => "skipped",
    }
}

fn source_cleanup(current: Option<SourceCleanup>, target: SourceCleanup) -> Verdict {
    use SourceCleanup::*;
    match (current, target) {
        (None, _) => Some(Apply),
        (Some(Pending | Skipped), Eligible) => Some(Apply),
        (Some(Pending | Eligible), Deleting) => Some(Apply),
        (Some(Pending | Eligible | Deleting | Skipped), Completed) => Some(Apply),
        (Some(Pending | Eligible), Skipped) => Some(Apply),
        // Deleted sources stay deleted; a late skip or eligibility is stale.
        (Some(Completed), Skipped | Eligible) => Some(Ignore),
        (Some(previous), next) if previous == next => Some(Ignore),
        _ => None,
    }
}

state_tokens! {
    enum OutboundLocalCleanup {
        NotApplicable => "not_applicable",
        Retained => "retained",
        Eligible => "eligible",
        Cleaning => "cleaning",
        Cleaned => "cleaned",
    }
}

fn outbound_local_cleanup(
    current: Option<OutboundLocalCleanup>,
    target: OutboundLocalCleanup,
) -> Verdict {
    use OutboundLocalCleanup::*;
    match (current, target) {
        (None, NotApplicable) => Some(Apply),
        // The completion policy may be re-applied until cleaning starts.
        (Some(NotApplicable | Retained | Eligible), Retained | Eligible) => Some(Apply),
        (Some(NotApplicable), NotApplicable) => Some(Ignore),
        (Some(Eligible), Cleaning) => Some(Apply),
        (Some(Cleaning), Cleaning) => Some(Ignore),
        // The caller authenticated a fresh hub receipt before reclaiming files.
        (Some(NotApplicable | Retained | Eligible | Cleaning), Cleaned) => Some(Apply),
        (Some(Cleaned), Cleaned) => Some(Ignore),
        _ => None,
    }
}

pub(crate) fn vocabularies() -> Vec<Vocabulary> {
    vec![
        ("status_machine", Machine::tokens()),
        ("report_llm_inference_job_status", ReportLlm::tokens()),
        ("video_processing_history_status", ProcessingHistory::tokens()),
        ("quarantine_item_status", Quarantine::tokens()),
        ("frame_annotation_export_job_status", FrameExport::tokens()),
        ("ai_dataset_export_artifact_status", DatasetExport::tokens()),
        ("dicom_export_job_status", DicomExport::tokens()),
        ("patient_examination_report_status", ExaminationReport::tokens()),
        ("hub_inbound_transfer_status", InboundTransfer::tokens()),
        ("hub_inbound_case_resolution_status", CaseResolution::tokens()),
        ("upload_job_source_cleanup_status", SourceCleanup::tokens()),
        ("hub_outbound_local_cleanup_status", OutboundLocalCleanup::tokens()),
    ]
}

fn run<S: StateToken>(
    current: &str,
    target: &str,
    table: fn(Option<S>, S) -> Verdict,
) -> Result<Decision, String> {
    let parse = |value: &str| S::parse_token(value).ok_or_else(|| format!("unknown status: {value}"));
    let current_status = if current.is_empty() { None } else { Some(parse(current)?) };
    let target_status = parse(target)?;
    table(current_status, target_status).ok_or_else(|| {
        let shown = if current.is_empty() { "<new>" } else { current };
        format!("illegal transition: {shown} -> {target}")
    })
}

fn decide(machine: Machine, current: &str, target: &str) -> Result<Decision, String> {
    match machine {
        Machine::ReportLlmInferenceJob => run(current, target, report_llm),
        Machine::VideoProcessingHistory => run(current, target, processing_history),
        Machine::QuarantineItem => run(current, target, quarantine),
        Machine::FrameAnnotationExportJob => run(current, target, frame_export),
        Machine::AiDatasetExportArtifact => run(current, target, dataset_export),
        Machine::DicomExportJob => run(current, target, dicom_export),
        Machine::PatientExaminationReport => run(current, target, examination_report),
        Machine::HubInboundTransfer => run(current, target, inbound_transfer),
        Machine::HubInboundCaseResolution => run(current, target, case_resolution),
        Machine::UploadJobSourceCleanup => run(current, target, source_cleanup),
        Machine::HubOutboundLocalCleanup => run(current, target, outbound_local_cleanup),
    }
}

/// True applies the target, False ignores it; unknown tokens and illegal
/// transitions raise ValueError. An empty current status is an unsaved row.
#[pyfunction]
pub(crate) fn decide_status_transition(machine: &str, current: &str, target: &str) -> PyResult<bool> {
    let parsed = Machine::parse(machine)
        .ok_or_else(|| PyValueError::new_err(format!("unknown status machine: {machine}")))?;
    decide(parsed, current, target)
        .map(|decision| decision == Apply)
        .map_err(|message| PyValueError::new_err(format!("{machine}: {message}")))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn currents<S: StateToken>() -> Vec<Option<S>> {
        std::iter::once(None).chain(S::all().iter().copied().map(Some)).collect()
    }

    /// Shared invariants every table must satisfy.
    ///
    /// `legacy` lists statuses that persisted rows may hold but no writer sets.
    fn check_table<S: StateToken>(
        table: fn(Option<S>, S) -> Verdict,
        finals: &[S],
        legacy: &[S],
    ) {
        for current in currents::<S>() {
            for &target in S::all() {
                let verdict = table(current, target);
                if verdict == Some(Apply) {
                    // Re-applying a reached status is defined (ignored or refreshed).
                    assert!(table(Some(target), target).is_some(), "{target:?} again");
                }
                if let Some(state) = current {
                    if finals.contains(&state) && state != target {
                        assert_ne!(verdict, Some(Apply), "final {state:?} -> {target:?}");
                    }
                }
            }
        }
        let mut reached: Vec<S> = S::all()
            .iter()
            .copied()
            .filter(|&target| table(None, target) == Some(Apply))
            .collect();
        assert!(!reached.is_empty(), "no initial status");
        loop {
            let before = reached.len();
            for from in reached.clone() {
                for &to in S::all() {
                    if table(Some(from), to) == Some(Apply) && !reached.contains(&to) {
                        reached.push(to);
                    }
                }
            }
            if reached.len() == before {
                break;
            }
        }
        for &status in S::all() {
            assert_eq!(
                reached.contains(&status),
                !legacy.contains(&status),
                "{status:?} reachability"
            );
        }
    }

    #[test]
    fn every_table_keeps_finals_final_and_reaches_every_status() {
        use ReportLlm as R;
        check_table(report_llm, &[R::Success, R::Cancelled], &[]);
        use ProcessingHistory as P;
        check_table(processing_history, &[P::Success, P::Failure, P::Cancelled], &[]);
        check_table(quarantine, &[], &[]);
        use FrameExport as F;
        check_table(frame_export, &[F::Completed, F::Failed, F::Lost], &[]);
        use DatasetExport as D;
        check_table(dataset_export, &[D::Failed], &[]);
        use DicomExport as X;
        check_table(dicom_export, &[X::Imported, X::Failed], &[]);
        check_table(examination_report, &[], &[]);
        use InboundTransfer as I;
        check_table(inbound_transfer, &[I::Inconsistent], &[I::Claimed, I::RetryWait]);
        check_table(case_resolution, &[], &[]);
        use SourceCleanup as S;
        check_table(source_cleanup, &[S::Completed], &[]);
        use OutboundLocalCleanup as O;
        check_table(outbound_local_cleanup, &[O::Cleaned], &[]);
    }

    #[test]
    fn domain_rules_hold() {
        // Report LLM: redelivery restarts failures, but success and cancellation stay final.
        assert_eq!(report_llm(Some(ReportLlm::Failure), ReportLlm::Running), Some(Apply));
        assert_eq!(report_llm(Some(ReportLlm::Success), ReportLlm::Failure), Some(Ignore));
        assert_eq!(report_llm(Some(ReportLlm::Cancelled), ReportLlm::Running), None);
        // Quarantine: only approved items are deleted.
        assert_eq!(quarantine(Some(Quarantine::Retained), Quarantine::Deleted), None);
        assert_eq!(quarantine(Some(Quarantine::Deleted), Quarantine::Retained), None);
        // Frame export: completion requires a running claim.
        assert_eq!(frame_export(Some(FrameExport::Queued), FrameExport::Completed), None);
        // Inbound transfer: only applied transfers become lost; inconsistent is terminal.
        assert_eq!(inbound_transfer(Some(InboundTransfer::Running), InboundTransfer::Lost), None);
        assert_eq!(
            inbound_transfer(Some(InboundTransfer::Inconsistent), InboundTransfer::Running),
            None
        );
        // Source cleanup: deletion needs eligibility or a pending source, never a skip.
        assert_eq!(source_cleanup(Some(SourceCleanup::Skipped), SourceCleanup::Deleting), None);
        assert_eq!(
            source_cleanup(Some(SourceCleanup::Deleting), SourceCleanup::Skipped),
            None
        );
        // Outbound cleanup: cleaning starts only from eligible.
        assert_eq!(
            outbound_local_cleanup(
                Some(OutboundLocalCleanup::Retained),
                OutboundLocalCleanup::Cleaning
            ),
            None
        );
        // Case resolution never returns to pending.
        assert_eq!(case_resolution(Some(CaseResolution::Linked), CaseResolution::Pending), None);
    }

    #[test]
    fn entry_point_fails_closed() {
        assert!(decide_status_transition("dicom_export_job", "", "received").unwrap());
        assert!(!decide_status_transition("dicom_export_job", "imported", "imported").unwrap());
        assert!(decide_status_transition("dicom_export_job", "imported", "received").is_err());
        assert!(decide_status_transition("dicom_export_job", "unknown", "imported").is_err());
        assert!(decide_status_transition("unknown_machine", "", "received").is_err());
        for &machine in Machine::ALL {
            assert!(decide_status_transition(machine.as_str(), "", "").is_err());
        }
    }
}
