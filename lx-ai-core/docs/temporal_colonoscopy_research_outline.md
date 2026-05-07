# Temporal-Aware AI Runtime for Colonoscopy Video Understanding

## Working Paper Title

**Temporal-Aware Hybrid Inference for Colonoscopy Video Understanding: A Lightweight Runtime for Stable Segment-Level Endoscopy Models**

## Core Thesis

Frame-wise colonoscopy AI is unstable when used directly for workflow decisions. A practical system should treat model outputs as noisy observations in a video sequence, then apply temporal reasoning before creating segments, annotations, or downstream actions. The implemented `lx-ai-core` prototype tests this idea with a local-only runtime that combines PyTorch inference, configurable temporal postprocessing, Markov smoothing, Viterbi state decoding, and optional Rust-accelerated primitives.

This is a research-runtime contribution, not a clinically validated diagnostic system.

## Abstract Draft

Deep learning models for colonoscopy commonly operate on individual frames, but clinical endoscopy workflows depend on temporally coherent events: outside-body intervals, low-quality views, instrument presence, mucosal inspection, bleeding, and lesion visibility. Frame-independent predictions often produce short spikes, fragmented segments, and unstable labels. We present a lightweight temporal-aware AI runtime for colonoscopy video understanding. The system separates model execution from clinical storage and application logic, accepts frame/video/signal/text/math model inputs, and provides temporal segment outputs with provenance and uncertainty. For video tasks, it supports threshold hysteresis, per-label thresholds, short-gap merging, independent binary Markov smoothing, scene-change diffusion, and Viterbi decoding for mutually exclusive state streams. The design is inspired by hybrid HMM/CRF ideas from the `devisions` experimental codebase while remaining dependency-free and suitable for integration into the LX ecosystem. The research project evaluates whether temporal inference improves segment stability, false-positive burst suppression, and workflow usability compared with raw frame-wise predictions.

## Paper Outline

### 1. Introduction

- Colonoscopy AI often starts from frame-wise classification or segmentation.
- Day-to-day usability requires stable video-level outputs, not isolated frame predictions.
- Common failure modes:
  - one-frame false positive spikes;
  - fragmented polyp/instrument/low-quality intervals;
  - stale predictions across scene changes;
  - oversized per-frame payloads that are difficult to use in annotation workflows.
- Research question: can lightweight temporal inference improve practical reliability without adding heavy model latency?

### 2. Background and Motivation

- Frame classification and segmentation in endoscopy.
- Temporal structure in colonoscopy videos:
  - outside-body entry/exit;
  - low-quality or occluded intervals;
  - mucosal inspection;
  - instrument interaction;
  - transient findings and sustained findings.
- Hybrid temporal methods:
  - hysteresis thresholding;
  - HMM-style belief updates;
  - CRF/Viterbi sequence decoding;
  - scene-change diffusion.
- Motivation from `devisions`:
  - rule/CNN emissions;
  - Markov belief filtering;
  - CRF-style online Viterbi;
  - fast per-frame overhead compared with feature extraction.

### 3. System Design

- `lx-ai-core` is a standalone local runtime boundary.
- It intentionally excludes:
  - Django models;
  - Celery workers;
  - database reads;
  - network calls;
  - raw-media transfer.
- Public contracts:
  - `InferenceRequest`;
  - `ModelSpec`;
  - `InferenceResult`;
  - `TemporalSegment`;
  - `MaskArtifact`;
  - `ScoreVector`;
  - `RunMetrics`.
- Supported modalities:
  - frame;
  - video;
  - signal;
  - text;
  - mathematical/vector systems.
- Supported initial backend:
  - PyTorch runtime with model cache and device-aware dispatch.

### 4. Temporal Inference Methods

#### 4.1 Hysteresis Segmentation

- A high threshold starts a segment.
- A lower threshold keeps a segment alive.
- Short gaps can be bridged with `max_gap`.
- Per-label thresholds support different operating points for labels such as `outside`, `low_quality`, `instrument`, and `polyp`.

#### 4.2 Independent Binary Markov Smoothing

- Each label is modeled as OFF/ON.
- Raw frame scores are treated as noisy emissions.
- `markov_stay_probability` models persistence.
- `markov_enter_probability` models rare event entry.
- Optional `change_scores` or inferred score changes diffuse the prior toward a less certain belief after likely scene changes.

#### 4.3 Viterbi State Decoding

- For mutually exclusive colonoscopy states, the system decodes one dominant state per frame.
- Default transition matrix is sticky.
- A supplied transition matrix can encode learned or expert prior knowledge.
- Outputs are converted to coherent `TemporalSegment` records.

#### 4.4 Uncertainty

- Binary entropy estimates ambiguity across multilabel scores.
- Margin uncertainty highlights predictions near 0.5.
- Uncertainty can identify intervals needing manual review.

### 5. Implementation

- Python package:
  - contracts;
  - runtime registry;
  - PyTorch backend;
  - postprocessing;
  - temporal inference module;
  - CLI.
- Rust/PyO3 extension:
  - threshold runs;
  - smoothing;
  - mask RLE encode/decode.
- Runtime optimizations:
  - device/dtype-aware model caching;
  - optional LRU model cache limit;
  - flat mask RLE encoding;
  - optional suppression of large per-frame score payloads.

### 6. Experimental Design

#### 6.1 Data

- Use de-identified colonoscopy videos already permitted for research.
- Use frame-level labels and/or existing segment annotations.
- Proposed labels:
  - outside;
  - low_quality;
  - mucosa;
  - instrument;
  - polyp;
  - blood;
  - stool/bubbles/occlusion if available.

#### 6.2 Baselines

- Raw frame-wise model scores.
- Simple thresholding.
- Hysteresis only.
- Markov smoothing.
- Viterbi decoding.
- Optional comparison with `devisions` HMM/CRF scripts on matched labels.

#### 6.3 Metrics

- Frame-level:
  - precision;
  - recall;
  - F1;
  - AUROC/AUPRC where appropriate.
- Segment-level:
  - segment IoU;
  - event-level precision/recall;
  - mean boundary error;
  - segment fragmentation rate;
  - false positive burst count;
  - missed sustained-event count.
- Workflow metrics:
  - number of candidate intervals shown to annotators;
  - manual correction burden;
  - inference runtime per frame;
  - output payload size.

#### 6.4 Ablations

- No temporal model.
- Hysteresis only.
- Hysteresis + gap merging.
- Markov smoothing with different stay/enter probabilities.
- Markov smoothing with scene-change diffusion.
- Viterbi with sticky prior.
- Viterbi with learned transition matrix.
- Score-vector output on/off.

### 7. Expected Results

- Temporal inference should reduce short false-positive bursts.
- Hysteresis should reduce segment fragmentation.
- Markov smoothing should improve sustained label stability.
- Viterbi should improve mutually exclusive state coherence.
- Scene-change diffusion should reduce stale-state persistence after abrupt transitions.
- Runtime overhead should remain small compared with frame model inference.

### 8. Limitations

- Temporal smoothing can hide short true positives if parameters are too conservative.
- Viterbi assumptions are inappropriate for genuinely multilabel events.
- Transition priors can encode dataset bias.
- Clinical usefulness requires prospective validation.
- Segment-level metrics depend strongly on annotation policy.

### 9. Conclusion

The proposed runtime moves colonoscopy AI from isolated frame outputs toward stable video understanding. The research contribution is a clean, local, dependency-light temporal inference layer that can be evaluated independently and later integrated into LX workflows.

## Research Project Instructions

### Phase 1: Define the Evaluation Set

1. Select 20-50 de-identified colonoscopy videos with diverse quality.
2. Ensure videos include outside-body, low-quality, mucosa, instrument, and lesion/finding intervals where possible.
3. Export frame-level labels or segment annotations into a neutral research format.
4. Freeze the evaluation split before tuning temporal parameters.

Deliverable: `evaluation_manifest.yaml` with video IDs, label availability, frame counts, and split assignment.

### Phase 2: Establish Baselines

1. Run the current frame-wise model on each video.
2. Store raw frame scores without temporal postprocessing.
3. Convert raw scores to segments with simple thresholds.
4. Compute frame-level and segment-level baseline metrics.

Deliverable: baseline metrics table and raw-score artifact set.

### Phase 3: Tune Temporal Models on a Development Split

1. Tune hysteresis thresholds per label.
2. Tune `max_gap` and `min_length`.
3. Tune Markov `stay_probability` and `enter_probability`.
4. Test scene-change diffusion using either supplied change scores or inferred frame-score volatility.
5. Tune Viterbi only for mutually exclusive state sets.

Deliverable: locked temporal config file with parameters and rationale.

### Phase 4: Blind Evaluation

1. Run all methods on the held-out evaluation split.
2. Do not change thresholds after seeing held-out results.
3. Compare:
   - raw frame thresholding;
   - hysteresis;
   - Markov smoothing;
   - Viterbi decoding.
4. Report confidence intervals using video-level bootstrapping.

Deliverable: final metrics report and plots.

### Phase 5: Human Workflow Review

1. Sample predicted segments from each method.
2. Ask annotators to rate whether segment boundaries are usable.
3. Track correction operations:
   - delete segment;
   - split segment;
   - merge segments;
   - adjust boundary;
   - relabel segment.
4. Compare annotation burden between raw and temporal methods.

Deliverable: workflow usability report.

### Phase 6: Integration Readiness

1. Keep `lx-ai-core` as the inference core.
2. Add `endoreg-db` integration only after the research API is stable.
3. Persist only validated `InferenceResult` summaries and local artifact references.
4. Run inference in a dedicated local worker queue.
5. Do not transmit raw media or master keys.

Deliverable: integration proposal for `endoreg-db` and `lx-annotate`.

## Minimum Experiment Config

```yaml
methods:
  raw_threshold:
    threshold: 0.5

  hysteresis:
    temporal_model: hysteresis
    threshold: 0.5
    low_threshold: 0.35
    min_length: 3
    max_gap: 5

  markov:
    temporal_model: markov
    threshold: 0.5
    markov_stay_probability: 0.97
    markov_enter_probability: 0.02
    markov_change_sensitivity: 1.0
    min_length: 3
    max_gap: 5

  viterbi_state:
    temporal_model: viterbi
    state_stay_probability: 0.97
    include_score_vectors: false
```

## Proposed Acceptance Criteria

- Temporal method reduces false positive burst count by at least 25% versus raw thresholding.
- Segment fragmentation decreases without more than a small predefined recall loss.
- Runtime overhead stays below 1 ms/frame for postprocessing on CPU.
- Annotator correction burden decreases on a blinded sample.
- All outputs remain local, validated, and reproducible.
