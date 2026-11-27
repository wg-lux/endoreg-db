//! Exports every native state vocabulary so Python mirrors are verified, never trusted.
use crate::state_token::Vocabulary;
use crate::{
    hls_state, hub_transfer_state, import_processing, import_state, lifecycle_state, state_enums,
    status_transition,
};
use pyo3::prelude::*;

fn all_vocabularies() -> Vec<Vocabulary> {
    [
        crate::clinical_workflow::vocabularies(),
        crate::media_decisions::vocabularies(),
        import_processing::vocabularies(),
        import_state::vocabularies(),
        lifecycle_state::vocabularies(),
        hls_state::vocabularies(),
        state_enums::vocabularies(),
        hub_transfer_state::vocabularies(),
        status_transition::vocabularies(),
    ]
    .into_iter()
    .flatten()
    .collect()
}

#[pyfunction]
pub(crate) fn native_state_vocabularies() -> Vec<Vocabulary> {
    all_vocabularies()
}

#[cfg(test)]
mod tests {
    use super::all_vocabularies;
    use std::collections::HashSet;

    #[test]
    fn vocabulary_names_and_tokens_are_unique_and_non_empty() {
        let vocabularies = all_vocabularies();
        let mut names = HashSet::new();
        for (name, tokens) in &vocabularies {
            assert!(names.insert(*name), "duplicate vocabulary name: {name}");
            assert!(!tokens.is_empty(), "empty vocabulary: {name}");
            let unique: HashSet<_> = tokens.iter().collect();
            assert_eq!(unique.len(), tokens.len(), "duplicate token in {name}");
            for token in tokens {
                assert!(
                    !token.is_empty()
                        && token
                            .bytes()
                            .all(|byte| byte.is_ascii_lowercase() || byte == b'_'),
                    "token {token:?} in {name} is not a snake_case wire token"
                );
            }
        }
    }
}
