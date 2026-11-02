//! One declaration per native token: enum variant, wire token, parser and vocabulary.
//!
//! Python mirrors are verified against `tokens()` at startup, so a token added
//! here without its Python mirror fails closed instead of drifting.

macro_rules! state_tokens {
    (
        $(#[$meta:meta])*
        enum $name:ident {
            $($variant:ident => $token:literal),+ $(,)?
        }
    ) => {
        $(#[$meta])*
        #[derive(Clone, Copy, Debug, Eq, PartialEq)]
        enum $name {
            $($variant),+
        }

        impl $name {
            const ALL: &'static [Self] = &[$(Self::$variant),+];

            const fn as_str(self) -> &'static str {
                match self {
                    $(Self::$variant => $token),+
                }
            }

            // Output-only vocabularies are exported and tested but never parsed.
            #[allow(dead_code)]
            fn parse(value: &str) -> Option<Self> {
                match value {
                    $($token => Some(Self::$variant),)+
                    _ => None,
                }
            }

            fn tokens() -> Vec<&'static str> {
                Self::ALL.iter().map(|value| value.as_str()).collect()
            }
        }
    };
}

pub(crate) use state_tokens;

/// Exported vocabulary row: stable vocabulary name and its tokens in declaration order.
pub(crate) type Vocabulary = (&'static str, Vec<&'static str>);

#[cfg(test)]
mod tests {
    state_tokens! {
        enum Sample {
            First => "first",
            Second => "second",
        }
    }

    #[test]
    fn every_token_round_trips_and_unknown_tokens_are_rejected() {
        assert_eq!(Sample::tokens(), vec!["first", "second"]);
        for value in Sample::ALL {
            assert_eq!(Sample::parse(value.as_str()), Some(*value));
        }
        for token in ["", "First", " first", "third"] {
            assert_eq!(Sample::parse(token), None);
        }
    }
}
