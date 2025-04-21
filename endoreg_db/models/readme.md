# DevLog
Phase 1: Stabilization and Refactoring

Here's a list of required fixes and refactoring steps based on the restructuring and the goal of modularizing FFmpeg usage:


Check Model Relationships and Constraints:

Review ForeignKey, ManyToManyField, OneToOneField definitions in restructured models. Ensure related_names are correct and unique where needed.
Check for potential circular dependencies introduced by the restructuring.
Verify database constraints (UniqueConstraint, etc.) are still appropriate.
Update Configuration and Paths:

Ensure constants like STORAGE_DIR, VIDEO_DIR, ANONYM_VIDEO_DIR, FRAME_DIR and the data_paths dictionary are correctly defined and used consistently.
Phase 2: Unit Testing

Once the code is running without obvious errors, implement unit tests:

Testing Strategy:

Use pytest or Django's built-in unittest.TestCase.
Use mocking (unittest.mock.patch) extensively to isolate units:
Mock file system operations (Path.exists, shutil.copy, cv2.imread, cv2.imwrite, os.mkdir, shutil.rmtree, etc.).
Mock the new FFmpeg wrapper functions (get_stream_info, assemble_video, transcode_video).
Mock external services or complex dependencies if necessary.
Use factories (like factory-boy) to create consistent test model instances.
Test both successful ("happy path") scenarios and failure conditions (e.g., file not found, invalid input, exceptions from mocked dependencies).
Core Model Tests:

VideoFile: Test creation (create_from_file), property access, state transitions, relationships, helper method logic (mocking dependencies). Test the main anonymize method orchestrator (mocking the underlying steps like frame creation and assembly).
Frame: Test creation, relationship to VideoFile, path generation (_frame_upload_path), and especially the anonymize method (testing ROI logic, all_black, color, file I/O mocking).
FFMpegMeta: Test create_from_file (mocking the FFmpeg wrapper), ensuring correct parsing of probe data for various scenarios (video only, video+audio, missing fields).
VideoMeta: Test create_from_file, properties (fps, duration, etc.), relationship to FFMpegMeta.
VideoImportMeta / SensitiveMeta: Test creation, field defaults, and any specific methods.
Process Tests:

Test the end-to-end anonymization process (video_file_anonymize._anonymize), mocking heavily to verify the orchestration logic, validation checks, transaction handling, and cleanup calls.
Test video import/creation logic.
Test frame extraction logic.
Utility Tests:

Test the new FFmpeg wrapper functions (mocking subprocess.run).
Test hash functions (get_video_hash, get_pdf_hash, etc.).
Test path generation utilities.
Phase 3: Documentation

Update Documentation:
Review and update README.md, readme.md, endoreg-db-model-documentation.md to reflect the new structure, key classes, and usage examples.
Ensure docstrings for all modified/new classes and methods are accurate.
Consider generating an updated model relationship diagram (e.g., using django-extensions).

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