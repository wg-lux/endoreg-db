Refactoring Plan:

Phase 1: Foundation & Standardization


Standardize State Management (VideoState & video_file_state.py):

Action: Analyze how VideoState is used across all modules and pipelines (pipe_1.py, pipe_2.py). Map out which functions require a certain state (pre-condition) and which functions set a certain state upon success (post-condition).
Refactor:
Establish strict pre-condition checks at the beginning of functions/pipelines where a specific state is required (e.g., anonymize requires frames_extracted). Fail early if the state is incorrect.
Ensure state fields (frames_extracted, frames_initialized, anonymized, sensitive_data_retrieved, etc.) are reliably updated within the same transaction as the operation they represent. Use update_fields in save() calls.
Remove redundant state checks within a pipeline if a prior step guarantees the required state.
Document the expected state transitions for major operations (frame extraction, anonymization, prediction).
Goal: Consistent, reliable, and understandable state tracking.

Standardize Error Handling & Logging:

Action: Review try...except blocks, logging levels (logger.info, logger.warning, logger.error), and return values (especially True/False from pipelines/steps).
Refactor:
Use consistent logging levels for similar events (e.g., start/end of major steps, errors, warnings for recoverable issues). Include relevant identifiers (like video.uuid) in log messages.
Consider defining specific custom exceptions (e.g., FrameExtractionError(Exception), AnonymizationError(Exception)) for clearer error identification, although standard exceptions might suffice if handled well.
Ensure functions that are part of a larger workflow (like steps in a pipeline) raise exceptions on failure rather than just logging and returning False, allowing the calling transaction block (@transaction.atomic) to handle rollback cleanly. Pipelines (pipe_1, pipe_2) can catch exceptions from their steps, log the failure, and return False.
Goal: Clearer error reporting, easier debugging, and robust transaction management.
Phase 2: Consolidating Core Logic

Refactor Frame Handling (video_file_frames.py):

Action: Consolidate all logic directly related to Frame objects and their physical files here.
Refactor:
Ensure _extract_frames handles extraction, _initialize_frames handles DB object creation/linking, and _delete_frames handles file deletion and state reset (without deleting DB objects, as per current logic).
Functions in other modules needing frame information (e.g., _extract_text_from_video_frames, _predict_video_pipeline, _censor_outside_frames) should only use helpers from this module (like get_frame_paths, get_frames, get_frame) to access frame data/paths. Remove any frame path generation or direct frame file access from other modules.
Verify that _initialize_frames correctly updates state.frames_initialized and state.frame_count.
Goal: A single, authoritative module for all frame-related operations.
Refactor Metadata Handling (video_file_meta.py):

Action: Consolidate logic related to VideoMeta (technical video properties) and SensitiveMeta (OCR'd data).
Refactor:
Ensure _update_video_meta (for technical specs) and _update_text_metadata (for sensitive data via OCR) are the primary entry points.
_update_text_metadata should use frame helpers (get_frame_paths) and OCR utilities, then call the update_or_create_sensitive_meta_from_dict logic.
Review the logic in _save_video_file.py that derives VideoFile fields from VideoMeta and SensitiveMeta. This logic is reasonable to keep within the save flow (as it links related models), but ensure it's clear and doesn't duplicate logic from the _update_*_meta functions. Ensure update_fields is used correctly.
Goal: Centralized handling of related metadata models.
Refactor AI & Prediction (video_file_ai.py):

Action: Focus this module purely on AI model loading, inference, and post-processing.
Refactor:
Ensure _predict_video_pipeline relies only on video_file_frames.py helpers for frame paths (get_frame_paths) and video_file_meta.py helpers for video properties (get_fps, get_crop_template).
Keep the separation where _predict_video_pipeline returns sequences, and the pipeline (pipe_1) handles saving these sequences and converting them to LabelVideoSegment objects (using _convert_sequences_to_db_segments from video_file_segments.py). This maintains separation of concerns.
Simplify _predict_video_entry if it's just a thin wrapper around _predict_video_pipeline.
Goal: Isolate AI logic, relying on other modules for input data.
Refactor Anonymization (video_file_anonymize.py):

Action: Focus this module on the anonymization process: censoring frames and assembling the new video.
Refactor:
Use video_file_segments.py helpers (_get_outside_frames or similar) to identify frames needing censoring.
Use video_file_frames.py helpers to get paths for censoring.
Use video_file_io.py helpers for managing temporary directories and the final output path.
Verify transaction handling and the on_commit cleanup (_cleanup_raw_assets) are robust and correctly use IO helpers for paths.
Goal: Streamlined and robust anonymization workflow.
Phase 3: Pipelines & Final Review

Refactor Pipelines (pipe_1.py, pipe_2.py):

Action: Review the orchestration logic within the pipelines.
Refactor:
Break down long sequences of operations within the with transaction.atomic(): block into smaller, private helper methods within the pipeline file (e.g., _step_extract_frames, _step_run_prediction, _step_create_segments). Each helper should perform one logical step and raise an exception on failure.
The main pipeline function then becomes a clearer sequence of calls to these steps within the transaction block.
Ensure the main pipeline function catches exceptions from steps, logs appropriately, and returns False, allowing the transaction to roll back.
Goal: Improved readability and maintainability of the orchestration logic.
Review VideoFile Model (video_file.py):

Action: Review the main model class.
Refactor:
Confirm that the methods assigned (e.g., extract_frames = _extract_frames) point to the correctly refactored helper functions in their respective modules.
Re-evaluate the custom save method (_save_video_file.py). Ensure its complexity is justified. Check if Django signals could simplify any part of it (though explicit logic in save is often preferred for clarity). Ensure get_or_create_state is called appropriately.
Verify properties (@property) like has_raw, is_processed are efficient and correct based on the underlying fields and state.
Goal: A clean, well-defined model interface.
Add/Improve Tests:

Action: This should be done incrementally throughout the refactoring process.
Refactor: Write unit tests for individual helper functions (especially IO, state, and logic-heavy functions). Write integration tests for the pipelines (pipe_1, pipe_2) that mock external dependencies (like AI models if necessary) but test the interaction between modules, state changes, file system operations (using tempfile or similar), and database interactions within a test database.
Goal: Ensure correctness, prevent regressions, and provide confidence in the refactored code.
This step-by-step plan allows for incremental refactoring, focusing on standardizing core aspects first before tackling the specific logic modules and pipelines. Remember to commit changes frequently and run tests after each significant step.