# Pipeline Documentation for `process_video_dir.py`

**File Path:** `endoreg_db/utils/pipelines/process_video_dir.py`

---

> This document outlines the multi-stage video processing pipeline implemented in the `process_video_dir.py` module, covering registration, validation, anonymization, and final state outcomes.



## 0. Start: Registering a New Video (before any pipeline)

- A new `VideoFile` record is created to register the video file.
- Linked to a `Center` and an `EndoscopyProcessor`.
- A `VideoMeta` object is created (or updated) by reading the technical information from the raw video (e.g., **fps**, **resolution**, **frame count**, **duration**).
- `Frame` objects are initialized in the database — one for each expected frame, but they are not yet extracted.
- A `VideoState` object is created to track the processing progress for this video.
- **No frames are extracted yet. No sensitive information or AI predictions exist yet.**
### Tables Involved

| Table Name   |
|--------------|
| VideoFile    |
| VideoMeta    |
| Frame        |
| VideoState   |

## Pipeline 1 Steps

---

### Step 1: Ensure VideoState Exists (`_pipe_1_step_ensure_state`)
- Ensures a tracking object (`VideoState`) exists for this video.

---

### Step 2: Update or Create VideoMeta (`_pipe_1_step_update_meta`)
- Extract technical metadata from the raw file.
- Update or create the `VideoMeta` object.

---

### Step 3: Extract Frames (`_pipe_1_step_extract_frames`)
- Using `ffmpeg`, the raw video is split into frames.
- Frames are saved to disk.
- `Frame` database entries are updated: `is_extracted=True`.
- `VideoState.frames_extracted` is set to `True`.

---

### Step 4: Extract Text (OCR) (`_pipe_1_step_extract_text`)
- A random sample of frames is passed through OCR.
- Text such as **Patient Name**, **Date of Birth (DOB)**, **IDs** is extracted.
- A `SensitiveMeta` object is created and filled with this information.
- `VideoState.text_meta_extracted` is set to `True`.

---

### Step 5: Predict Labels (AI Prediction) (`_pipe_1_step_predict`)
- An AI model is run on frames to predict medical labels (e.g., **polyp**, **normal**, **outside**).
- Smoothed predictions are generated over frames.
- A `VideoPredictionMeta` is created to store AI model info and predictions.
- `VideoState.initial_prediction_completed` is set to `True`.

---

### Step 6: Create Label Segments (`_pipe_1_step_create_segments`)
- Predictions are converted into labeled segments (`LabelVideoSegment`).
- Each segment is linked to frame ranges and specific labels.
- A `LabelVideoSegmentState` is created for each segment to track validation.
- `VideoState.lvs_created` is set to `True`.

---
## End of PIPE 1

At this point in the pipeline:

- Frames are **extracted** and **saved**.
- OCR **text is extracted** and **stored**.
- AI **predictions are made**.
- **Label segments are created** based on predictions.
- **Sensitive information** (e.g. patient identifiers) is stored.
- `VideoState` is updated to reflect all completed steps.

---

### Tables Involved

| Table Name               |
|--------------------------|
| `VideoFile`              |
| `VideoMeta`              |
| `Frame`                  |
| `VideoState`             |
| `SensitiveMeta`          |
| `VideoPredictionMeta`    |
| `LabelVideoSegment`      |
| `LabelVideoSegmentState` |

---
## 2. VALIDATION Phase: Review and Approval

**Goal:** Confirm that automated results are correct before anonymization.

### Steps

- A human reviewer (or automated test) opens each `LabelVideoSegment` and reviews it.
- Each segment’s state is updated:  
  `LabelVideoSegmentState.is_validated = True`
- The extracted sensitive metadata (e.g., names, DOB) in `SensitiveMeta` is reviewed.
- Sensitive metadata state fields are set:  
  `dob_verified = True`  
  `names_verified = True`
- If **any** of these validations fail, processing **must stop** and be corrected.
- **Only** when **all** validations are successfully marked, the video is allowed to proceed to **PIPE 2**.

---

### Tables Involved

| Table Name               |
|--------------------------|
| `LabelVideoSegmentState` |
| `SensitiveMeta`          |

---
## 3. PIPELINE 2: Anonymization and Cleanup

**Goal:** Prepare a safe, anonymized version of the video.

---

### Pipeline 2 Major Steps

---

#### Step 1: Ensure VideoState Exists Again (`_pipe_2_step_ensure_state`)
- Confirm that the `VideoState` tracking object is still present.

---

#### Step 2: Ensure Frames Available (`_pipe_2_step_extract_frames`)
- Check if extracted frames are available.
- If frames were deleted or are missing, **re-extract** them from the raw video file.

---

#### Step 3: Anonymize Video (`_pipe_2_step_anonymize`)
- Frames labeled as `"outside"` are blacked out.
- Remaining frames are masked using **endoscopy ROI** (Region of Interest).
- New anonymized frames are created and saved temporarily.
- A new anonymized video is assembled from these frames.
- New video file path is saved in:  
  `VideoFile.processed_file`
- Video hash is calculated and saved in:  
  `VideoFile.processed_video_hash`
- `VideoState.anonymized = True`

---

#### Step 4: Delete Raw Files and Frames
- Raw video file and extracted frames are **permanently deleted**.
- `VideoState.frames_extracted = False`

---

#### Step 5: Delete SensitiveMeta (`_pipe_2_step_delete_sensitive_meta`)
- The `SensitiveMeta` object is deleted from the database.
- `VideoFile.sensitive_meta = None`

---

### At the End of PIPE 2:

- Only the **anonymized processed video** remains.
- **All sensitive data** and **raw video/frames** are permanently deleted.
- **Technical metadata** and **AI prediction audit data** are retained.

---

### Tables Involved

| Table Name      | Notes                            |
|------------------|----------------------------------|
| `VideoFile`      | Updated with processed file info |
| `Frame`          | Frames used, then deleted        |
| `VideoState`     | Tracks anonymization status      |
| `SensitiveMeta`  | **Deleted after anonymization**  |

---
## 4. FINAL STATE

### Database Contains:

- The **processed, safe, anonymized video**:  
  `VideoFile.processed_file`
- **Technical information** about the video:  
  `VideoMeta`
- **Medical label segments** for audit:  
  `LabelVideoSegment` and `LabelVideoSegmentState`
- **AI prediction information** for audit:  
  `VideoPredictionMeta`
- **Sensitive patient information** has been **permanently deleted**
- **Raw extracted frames** and the **original raw video file** have been **permanently deleted**

---

### `VideoState` Summary:

- `frames_extracted = False`  *(raw frames are gone)*
- `anonymized = True`  *(anonymized video is ready)*

---

### Tables Overview

| Table Name              | Purpose                                                                 |
|--------------------------|-------------------------------------------------------------------------|
| `VideoFile`              | Main entry linking video paths, processed file, and metadata references |
| `VideoMeta`              | Technical information (fps, duration, frame count, etc.)                |
| `Frame`                  | Individual frame images from the video *(deleted in PIPE 2)*            |
| `VideoState`             | Tracks pipeline processing steps and their completion                   |
| `SensitiveMeta`          | Patient-sensitive data from OCR *(deleted in PIPE 2)*                   |
| `VideoPredictionMeta`    | Information about the AI model and predictions                          |
| `LabelVideoSegment`      | Video segments created from AI predictions                              |
| `LabelVideoSegmentState` | Tracks human validation status for each segment                         |

---
### Note: on Frame Deletion and Data Integrity

Although the frames are permanently deleted after the entire execution of Pipeline 1 and Pipeline 2, the necessary data and information about these frames are still populated in the database. Therefore, a script is required to utilize the correct imported data and **re-extract potential frames** from the original video.

This process ensures that the required frame data can be retrieved and used even after deletion.


