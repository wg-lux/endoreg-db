# DevLog

# EndoReg DB Models
Summary by submodules.

## Administration
`endoreg_db.models.administration`

## Label
Includes `Label`, `LabelSet`, `LabelVideoSegment`, `ImageClassificationAnnotation`. `LabelVideoSegment` and `ImageClassificationAnnotation` now link directly to `VideoFile` and `Frame` respectively.

## Media
Includes `VideoFile` (the unified model for videos), `Frame`, `ImageFile`, `DocumentFile`.

## Medical
Includes `Patient`, `PatientExamination`, `PatientFinding`, `EndoscopyProcessor`, `Endoscope`, etc.

## Metadata
Includes `ModelMeta`, `VideoMeta`, `SensitiveMeta`, `VideoPredictionMeta`, etc. `VideoMeta` and `VideoPredictionMeta` now link directly to `VideoFile`.

## Other
Includes `InformationSource`.

## Requirement
*(No changes needed)*

## Rule
*(No changes needed)*

## State
Includes `VideoState`, `ReportState`, etc. `VideoState` now links directly to `VideoFile`.