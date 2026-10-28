//! Durable import work stack. Python persists this value; only Rust advances it.
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use serde::{Deserialize, Serialize};

#[derive(Clone, Copy, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
enum Stage {
    PrepareArtifact,
    PublishArtifact,
    CompleteUpload,
}

#[derive(Clone, Copy, Debug, Deserialize, Serialize)]
#[serde(rename_all = "snake_case")]
enum MediaKind {
    Video,
    Report,
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
        event: &str,
        media_id: Option<u64>,
        source_sha256: Option<&str>,
    ) -> Result<(), String> {
        self.validate()?;
        match event {
            "validate" => (),
            "bind_source" => {
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
            "prepare" if self.remaining.first() == Some(&Stage::PrepareArtifact) => (),
            "prioritize" if !self.remaining.is_empty() => self.priority = 1,
            "reset" => {
                self.remaining = STAGES.to_vec();
                self.checkpoint = None;
            }
            "prepared" => {
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
            "published" if self.remaining.first() == Some(&Stage::PublishArtifact) => {
                self.remaining.remove(0);
            }
            "published" if self.remaining.first() == Some(&Stage::CompleteUpload) => (),
            "published" if self.remaining.is_empty() => (),
            "completed" if self.remaining.first() == Some(&Stage::CompleteUpload) => {
                self.remaining.clear();
                self.priority = 0;
            }
            "completed" if self.remaining.is_empty() => (),
            _ => return Err(format!("Invalid import processing stack event: {event}")),
        }
        self.validate()
    }
}

#[pyfunction]
pub(crate) fn new_import_processing_stack(reference: &str, media_type: &str) -> PyResult<String> {
    let kind = match media_type {
        "video" => MediaKind::Video,
        "report" => MediaKind::Report,
        _ => return Err(PyValueError::new_err("Unknown import media type")),
    };
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
    let mut stack: ProcessingStack =
        serde_json::from_str(payload).map_err(|error| PyValueError::new_err(error.to_string()))?;
    stack
        .apply(event, media_id, source_sha256)
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
        assert!(state.apply("published", None, None).is_err());
        state
            .apply("bind_source", None, Some(&"b".repeat(64)))
            .unwrap();
        assert!(state
            .apply("bind_source", None, Some(&"c".repeat(64)))
            .is_err());
        state.apply("prepared", Some(7), None).unwrap();
        let mut restored: ProcessingStack =
            serde_json::from_str(&serde_json::to_string(&state).unwrap()).unwrap();
        restored.apply("prepared", Some(7), None).unwrap();
        assert!(restored.apply("prepared", Some(8), None).is_err());
        restored.apply("prioritize", None, None).unwrap();
        assert_eq!(restored.priority, 1);
        restored.apply("published", None, None).unwrap();
        restored.apply("completed", None, None).unwrap();
        restored.apply("completed", None, None).unwrap();
        assert_eq!(restored.reference, state.reference);
        assert!(restored.remaining.is_empty());
        assert!(restored.apply("prioritize", None, None).is_err());
    }
    #[test]
    fn rejects_corrupt_or_reordered_stack() {
        let mut state = stack();
        state.remaining = vec![Stage::PrepareArtifact, Stage::CompleteUpload];
        assert!(state.validate().is_err());
        state.remaining = vec![Stage::CompleteUpload];
        assert!(state.validate().is_err());
    }
}
