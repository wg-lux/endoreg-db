use pyo3::prelude::*;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum StorageProfile {
    StrictAppEncrypted,
    FsEncryptedStreaming,
    HybridDefault,
}

impl StorageProfile {
    const fn as_str(self) -> &'static str {
        match self {
            Self::StrictAppEncrypted => "strict_app_encrypted",
            Self::FsEncryptedStreaming => "fs_encrypted_streaming",
            Self::HybridDefault => "hybrid_default",
        }
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum PayloadKind {
    VideoRaw,
    VideoProcessed,
    ReportPdf,
    Metadata,
    Sidecar,
    Manifest,
}

impl PayloadKind {
    const fn as_str(self) -> &'static str {
        match self {
            Self::VideoRaw => "video_raw",
            Self::VideoProcessed => "video_processed",
            Self::ReportPdf => "report_pdf",
            Self::Metadata => "metadata",
            Self::Sidecar => "sidecar",
            Self::Manifest => "manifest",
        }
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum StoragePolicy {
    AppEncrypted,
    FsStreamable,
}

impl StoragePolicy {
    const fn as_str(self) -> &'static str {
        match self {
            Self::AppEncrypted => "app_encrypted",
            Self::FsStreamable => "fs_streamable",
        }
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
struct StoragePolicyRow {
    profile: StorageProfile,
    payload_kind: PayloadKind,
    storage_policy: StoragePolicy,
}

static STORAGE_PROFILE_POLICY_ROWS: [StoragePolicyRow; 18] = [
    StoragePolicyRow {
        profile: StorageProfile::StrictAppEncrypted,
        payload_kind: PayloadKind::VideoRaw,
        storage_policy: StoragePolicy::AppEncrypted,
    },
    StoragePolicyRow {
        profile: StorageProfile::StrictAppEncrypted,
        payload_kind: PayloadKind::VideoProcessed,
        storage_policy: StoragePolicy::AppEncrypted,
    },
    StoragePolicyRow {
        profile: StorageProfile::StrictAppEncrypted,
        payload_kind: PayloadKind::ReportPdf,
        storage_policy: StoragePolicy::AppEncrypted,
    },
    StoragePolicyRow {
        profile: StorageProfile::StrictAppEncrypted,
        payload_kind: PayloadKind::Metadata,
        storage_policy: StoragePolicy::AppEncrypted,
    },
    StoragePolicyRow {
        profile: StorageProfile::StrictAppEncrypted,
        payload_kind: PayloadKind::Sidecar,
        storage_policy: StoragePolicy::AppEncrypted,
    },
    StoragePolicyRow {
        profile: StorageProfile::StrictAppEncrypted,
        payload_kind: PayloadKind::Manifest,
        storage_policy: StoragePolicy::AppEncrypted,
    },
    StoragePolicyRow {
        profile: StorageProfile::FsEncryptedStreaming,
        payload_kind: PayloadKind::VideoRaw,
        storage_policy: StoragePolicy::FsStreamable,
    },
    StoragePolicyRow {
        profile: StorageProfile::FsEncryptedStreaming,
        payload_kind: PayloadKind::VideoProcessed,
        storage_policy: StoragePolicy::FsStreamable,
    },
    StoragePolicyRow {
        profile: StorageProfile::FsEncryptedStreaming,
        payload_kind: PayloadKind::ReportPdf,
        storage_policy: StoragePolicy::AppEncrypted,
    },
    StoragePolicyRow {
        profile: StorageProfile::FsEncryptedStreaming,
        payload_kind: PayloadKind::Metadata,
        storage_policy: StoragePolicy::AppEncrypted,
    },
    StoragePolicyRow {
        profile: StorageProfile::FsEncryptedStreaming,
        payload_kind: PayloadKind::Sidecar,
        storage_policy: StoragePolicy::AppEncrypted,
    },
    StoragePolicyRow {
        profile: StorageProfile::FsEncryptedStreaming,
        payload_kind: PayloadKind::Manifest,
        storage_policy: StoragePolicy::AppEncrypted,
    },
    StoragePolicyRow {
        profile: StorageProfile::HybridDefault,
        payload_kind: PayloadKind::VideoRaw,
        storage_policy: StoragePolicy::AppEncrypted,
    },
    StoragePolicyRow {
        profile: StorageProfile::HybridDefault,
        payload_kind: PayloadKind::VideoProcessed,
        storage_policy: StoragePolicy::FsStreamable,
    },
    StoragePolicyRow {
        profile: StorageProfile::HybridDefault,
        payload_kind: PayloadKind::ReportPdf,
        storage_policy: StoragePolicy::AppEncrypted,
    },
    StoragePolicyRow {
        profile: StorageProfile::HybridDefault,
        payload_kind: PayloadKind::Metadata,
        storage_policy: StoragePolicy::AppEncrypted,
    },
    StoragePolicyRow {
        profile: StorageProfile::HybridDefault,
        payload_kind: PayloadKind::Sidecar,
        storage_policy: StoragePolicy::AppEncrypted,
    },
    StoragePolicyRow {
        profile: StorageProfile::HybridDefault,
        payload_kind: PayloadKind::Manifest,
        storage_policy: StoragePolicy::AppEncrypted,
    },
];

#[pyfunction]
pub(crate) fn storage_profile_policy_rows() -> Vec<(&'static str, &'static str, &'static str)> {
    STORAGE_PROFILE_POLICY_ROWS
        .iter()
        .map(|row| {
            (
                row.profile.as_str(),
                row.payload_kind.as_str(),
                row.storage_policy.as_str(),
            )
        })
        .collect()
}

#[cfg(test)]
mod tests {
    use super::{PayloadKind, StorageProfile, STORAGE_PROFILE_POLICY_ROWS};
    use std::collections::BTreeSet;

    #[test]
    fn storage_profile_policy_rows_are_exhaustive() {
        let profiles = [
            StorageProfile::StrictAppEncrypted,
            StorageProfile::FsEncryptedStreaming,
            StorageProfile::HybridDefault,
        ];
        let payload_kinds = [
            PayloadKind::VideoRaw,
            PayloadKind::VideoProcessed,
            PayloadKind::ReportPdf,
            PayloadKind::Metadata,
            PayloadKind::Sidecar,
            PayloadKind::Manifest,
        ];

        let rows: BTreeSet<(&str, &str)> = STORAGE_PROFILE_POLICY_ROWS
            .iter()
            .map(|row| (row.profile.as_str(), row.payload_kind.as_str()))
            .collect();

        assert_eq!(rows.len(), profiles.len() * payload_kinds.len());
        for profile in profiles {
            for payload_kind in payload_kinds {
                assert!(rows.contains(&(profile.as_str(), payload_kind.as_str())));
            }
        }
    }
}
