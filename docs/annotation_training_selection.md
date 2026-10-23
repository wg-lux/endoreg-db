# Reviewed training annotation selection

Scope and readiness: [ProfessionalProductFoundations.yml](../feature-tracking/ProfessionalProductFoundations.yml),
criterion `reviewed_active_learning`. Selection belongs to
`endoreg_db.services.datasets.training_selection`, policy `manual_or_confirmed_v1`.

## Eligibility

- Membership in the selected `AIDataSet` is mandatory.
- Direct frame annotations need an explicit manual information source accepted
  by `manual_frame_annotation_preference_filter`. They have no separate
  confirmation field; an annotator name or general video approval does not
  confirm an individual prediction.
- Frame rows generated automatically from segments are not independent human
  annotations. Their eligible source segments must belong to the dataset.
- Segments qualify when `state.is_validated` is true, or when they have a manual
  source without prediction metadata or a prediction source type.
- Unconfirmed predictions and unclassified sources are excluded. Confirmed
  prediction segments retain their original source and segment identity.
- Segment labels map in memory onto existing frames in the half-open interval
  `[start_frame_number, end_frame_number)`. Selection writes neither annotation
  rows nor frame coordinates.
- Conflicting eligible values for one frame and label abort manifest creation.
- Unknown labels have mask `0` unless the caller explicitly enables
  `treat_unlabeled_as_negative`.

## Manifest request

Send `POST /api/settings/application/ai_datasets/{dataset_id}/training_manifest/`
through the deployment proxy. The backend route omits the proxy's `/api` prefix.

| Field | Meaning or default |
| --- | --- |
| `label_set_id` | Selected `LabelSet` identifier |
| `annotation_source_scope` | `all` by default; `frame_only` or `segment_only` restrict the eligible source kinds |
| `treat_unlabeled_as_negative` | `false` |
| `include_file_paths` | `false` |
| `information_source_names` | Additional filter on both source kinds; cannot bypass eligibility |

`annotation_ids_by_label` and `segment_ids_by_label` retain separate identities
in manifest provenance. Annotation provenance establishes label origin;
[video frame provenance](video_storage_normalization.md#out-of-band-frame-provenance-and-independent-recording-clocks)
establishes media ancestry. Neither substitutes for the other.

## Media and compatibility

Manifest export and training reads still require the clinical media gates.
Extraction status does not establish annotation confirmation. `lx-ai-core`
receives typed processed-frame references, labels and masks; it does not read
the database. See [processed training streams](training-streams.md).

The frame-bucket endpoint remains diagnostic and may show unconfirmed predictions;
its counts do not authorize training. Existing datasets without explicit sources
may yield fewer or no samples; missing approvals are never added automatically.
The separate SQL loader in the lx-ai checkout does not use this backend contract.
