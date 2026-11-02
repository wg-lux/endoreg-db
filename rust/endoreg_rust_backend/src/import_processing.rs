//! Durable import work stack. Python persists this value; only Rust advances it.
use crate::state_token::{state_tokens, Vocabulary};
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use serde::{Deserialize, Serialize};

state_tokens! {
    #[derive(Deserialize, Serialize)]
    #[serde(rename_all = "snake_case")]
    enum Stage {
        PrepareArtifact => "prepare_artifact",
        PublishArtifact => "publish_artifact",
        CompleteUpload => "complete_upload",
    }
}

state_tokens! {
    #[derive(Deserialize, Serialize)]
    #[serde(rename_all = "snake_case")]
    enum MediaKind {
        Video => "video",
        Report => "report",
    }
}

state_tokens! {
    enum ImportEvent {
        Validate => "validate",
        BindSource => "bind_source",
        Prepare => "prepare",
        AdoptPublished => "adopt_published",
        Prioritize => "prioritize",
        Reset => "reset",
        Prepared => "prepared",
        Published => "published",
        Completed => "completed",
    }
}

pub(crate) fn vocabularies() -> Vec<Vocabulary> {
    vec![
        ("import_processing_stage", Stage::tokens()),
        ("import_media_kind", MediaKind::tokens()),
        ("import_processing_event", ImportEvent::tokens()),
    ]
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
struct Checkpoint {
    reference: String,
    media_id: u64,
}

#[derive(Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct ProcessingStack {
    version: u8,
    reference: String,
    media_type: MediaKind,
    remaining: Vec<Stage>,
    checkpoint: Option<Checkpoint>,
    priority: u8,
    #[serde(default)]
    source_sha256: Option<String>,
}

const STAGES: [Stage; 3] = [
    Stage::PrepareArtifact,
    Stage::PublishArtifact,
    Stage::CompleteUpload,
];

impl ProcessingStack {
    fn validate(&self) -> Result<(), String> {
        let reference = self.reference.replace('-', "");
        if self.version != 1
            || reference.len() != 32
            || !reference.bytes().all(|b| b.is_ascii_hexdigit())
            || self.priority > 1
            || self.source_sha256.as_ref().is_some_and(|hash| {
                hash.len() != 64 || !hash.bytes().all(|b| b.is_ascii_hexdigit())
            })
        {
            return Err("Invalid import processing stack identity or version".into());
        }
        if self.remaining.len() > STAGES.len()
            || self.remaining != STAGES[STAGES.len() - self.remaining.len()..]
        {
            return Err("Import processing stack is not an ordered suffix".into());
        }
        match &self.checkpoint {
            Some(checkpoint)
                if checkpoint.reference == self.reference
                    && checkpoint.media_id > 0
                    && self.remaining.len() < STAGES.len() =>
            {
                Ok(())
            }
            None if self.remaining.len() == STAGES.len() => Ok(()),
            _ => Err("Import processing stack checkpoint does not match its reference".into()),
        }
    }

    fn apply(
        &mut self,
        event: ImportEvent,
        media_id: Option<u64>,
        source_sha256: Option<&str>,
    ) -> Result<(), String> {
        use ImportEvent as Event;

        self.validate()?;
        match event {
            Event::Validate => (),
            Event::BindSource => {
                let hash = source_sha256.ok_or("Source binding requires a hash")?;
                if self
                    .source_sha256
                    .as_ref()
                    .is_some_and(|bound| bound != hash)
                {
                    return Err("Import source binding cannot change".into());
                }
                self.source_sha256 = Some(hash.into());
            }
            Event::Prepare if self.remaining.first() == Some(&Stage::PrepareArtifact) => (),
            Event::AdoptPublished => {
                // Callers supply verified publication evidence, never a stage plan.
                if self.checkpoint.is_none()
                    || self.remaining.first() == Some(&Stage::PublishArtifact)
                {
                    self.apply(Event::Prepared, media_id, None)?;
                } else if self
                    .checkpoint
                    .as_ref()
                    .map(|checkpoint| checkpoint.media_id)
                    != media_id
                {
                    return Err("Published artifact does not match the checkpoint media".into());
                }
                self.apply(Event::Published, None, None)?;
            }
            Event::Prioritize if !self.remaining.is_empty() => self.priority = 1,
            Event::Reset => {
                self.remaining = STAGES.to_vec();
                self.checkpoint = None;
            }
            Event::Prepared => {
                let checkpoint = Checkpoint {
                    reference: self.reference.clone(),
                    media_id: media_id
                        .filter(|id| *id > 0)
                        .ok_or("Prepared stage requires a media identifier")?,
                };
                if self.remaining.first() == Some(&Stage::PrepareArtifact) {
                    self.checkpoint = Some(checkpoint);
                    self.remaining.remove(0);
                } else if self.remaining.first() != Some(&Stage::PublishArtifact)
                    || self.checkpoint.as_ref() != Some(&checkpoint)
                {
                    return Err("Invalid prepared checkpoint transition".into());
                }
            }
            Event::Published if self.remaining.first() == Some(&Stage::PublishArtifact) => {
                self.remaining.remove(0);
            }
            Event::Published if self.remaining.first() == Some(&Stage::CompleteUpload) => (),
            Event::Published if self.remaining.is_empty() => (),
            Event::Completed if self.remaining.first() == Some(&Stage::CompleteUpload) => {
                self.remaining.clear();
                self.priority = 0;
            }
            Event::Completed if self.remaining.is_empty() => (),
            _ => {
                return Err(format!(
                    "Invalid import processing stack event: {}",
                    event.as_str()
                ))
            }
        }
        self.validate()
    }
}

#[pyfunction]
pub(crate) fn new_import_processing_stack(reference: &str, media_type: &str) -> PyResult<String> {
    let kind = MediaKind::parse(media_type)
        .ok_or_else(|| PyValueError::new_err("Unknown import media type"))?;
    let stack = ProcessingStack {
        version: 1,
        reference: reference.into(),
        media_type: kind,
        remaining: STAGES.to_vec(),
        checkpoint: None,
        priority: 0,
        source_sha256: None,
    };
    stack.validate().map_err(PyValueError::new_err)?;
    serde_json::to_string(&stack).map_err(|error| PyValueError::new_err(error.to_string()))
}

#[pyfunction]
#[pyo3(signature = (payload, event, media_id=None, source_sha256=None))]
pub(crate) fn transition_import_processing_stack(
    payload: &str,
    event: &str,
    media_id: Option<u64>,
    source_sha256: Option<&str>,
) -> PyResult<String> {
    let parsed_event = ImportEvent::parse(event).ok_or_else(|| {
        PyValueError::new_err(format!("Invalid import processing stack event: {event}"))
    })?;
    let mut stack: ProcessingStack =
        serde_json::from_str(payload).map_err(|error| PyValueError::new_err(error.to_string()))?;
    stack
        .apply(parsed_event, media_id, source_sha256)
        .map_err(PyValueError::new_err)?;
    serde_json::to_string(&stack).map_err(|error| PyValueError::new_err(error.to_string()))
}

#[cfg(test)]
mod tests {
    use super::*;
    fn stack() -> ProcessingStack {
        ProcessingStack {
            version: 1,
            reference: "a".repeat(32),
            media_type: MediaKind::Video,
            remaining: STAGES.to_vec(),
            checkpoint: None,
            priority: 0,
            source_sha256: None,
        }
    }
    #[test]
    fn restart_preserves_reference_and_remaining_work() {
        let mut state = stack();
        assert!(state.apply(ImportEvent::Published, None, None).is_err());
        state
            .apply(ImportEvent::BindSource, None, Some(&"b".repeat(64)))
            .unwrap();
        assert!(state
            .apply(ImportEvent::BindSource, None, Some(&"c".repeat(64)))
            .is_err());
        state.apply(ImportEvent::Prepared, Some(7), None).unwrap();
        let mut restored: ProcessingStack =
            serde_json::from_str(&serde_json::to_string(&state).unwrap()).unwrap();
        restored
            .apply(ImportEvent::Prepared, Some(7), None)
            .unwrap();
        assert!(restored
            .apply(ImportEvent::Prepared, Some(8), None)
            .is_err());
        restored.apply(ImportEvent::Prioritize, None, None).unwrap();
        assert_eq!(restored.priority, 1);
        restored.apply(ImportEvent::Published, None, None).unwrap();
        restored.apply(ImportEvent::Completed, None, None).unwrap();
        restored.apply(ImportEvent::Completed, None, None).unwrap();
        assert_eq!(restored.reference, state.reference);
        assert!(restored.remaining.is_empty());
        assert!(restored.apply(ImportEvent::Prioritize, None, None).is_err());
    }
    #[test]
    fn rejects_corrupt_or_reordered_stack() {
        let mut state = stack();
        state.remaining = vec![Stage::PrepareArtifact, Stage::CompleteUpload];
        assert!(state.validate().is_err());
        state.remaining = vec![Stage::CompleteUpload];
        assert!(state.validate().is_err());
    }

    #[test]
    fn published_adoption_owns_stage_selection_and_preserves_media_identity() {
        let mut state = stack();
        state
            .apply(ImportEvent::AdoptPublished, Some(7), None)
            .unwrap();
        assert_eq!(state.remaining, vec![Stage::CompleteUpload]);
        state
            .apply(ImportEvent::AdoptPublished, Some(7), None)
            .unwrap();
        assert!(state
            .apply(ImportEvent::AdoptPublished, Some(8), None)
            .is_err());
        state.apply(ImportEvent::Completed, None, None).unwrap();
        state
            .apply(ImportEvent::AdoptPublished, Some(7), None)
            .unwrap();
        assert!(state
            .apply(ImportEvent::AdoptPublished, Some(8), None)
            .is_err());
        assert!(state.remaining.is_empty());
    }

    #[test]
    fn persisted_serde_names_equal_exported_tokens() {
        for stage in Stage::ALL {
            let persisted = serde_json::to_string(stage).unwrap();
            assert_eq!(persisted, format!("\"{}\"", stage.as_str()));
        }
        for kind in MediaKind::ALL {
            let persisted = serde_json::to_string(kind).unwrap();
            assert_eq!(persisted, format!("\"{}\"", kind.as_str()));
        }
    }
}
