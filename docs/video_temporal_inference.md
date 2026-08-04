# Video Temporal Inference

Temporal inference converts per-frame model scores into prediction
`LabelVideoSegment` rows. The endoreg-db service runs frame scoring first, then
delegates temporal post-processing to `lx-ai-core`.

## Entry Point

The media API endpoint is:

```http
POST /api/media/videos/<video_id>/segments/rerun-predictions/
```

Relevant request fields are collected as `temporal_options` by
`endoreg_db.services.video_temporal_inference.extract_temporal_options`.
Only snake_case option names listed in `TEMPORAL_OPTION_KEYS` are forwarded.

## Smoothing Options

Temporal smoothing is controlled by two options:

- `temporal_smoothing_enabled`: optional boolean, default `true`.
- `smoothing_window_seconds`: optional non-negative number, default `1.0`.

When `temporal_smoothing_enabled` is omitted or `true`, endoreg-db applies a
centered rolling mean against each scored frame's authoritative presentation
timestamp. The window therefore represents elapsed media time even when the
time between consecutive frames is not uniform.

Example:

```json
{
  "temporal_smoothing_enabled": true,
  "smoothing_window_seconds": 1.0
}
```

This smooths each score using the frames whose presentation timestamps fall
within the centered one-second window. It does not convert the duration through
a nominal frame rate.

The options sent to `lx-ai-core` use its identity frame-count settings:

```json
{
  "smoothing_window": 1,
  "min_length": 1,
  "max_gap": 0
}
```

When `temporal_smoothing_enabled` is `false`, endoreg-db ignores the requested
window duration and leaves the score rows unchanged:

```json
{
  "temporal_smoothing_enabled": false,
  "smoothing_window_seconds": 3.0
}
```

The normalized history payload records
`temporal_smoothing_enabled=false` and `smoothing_window_seconds=0.0` so later
inspection can distinguish "disabled" from a normal one-second smoothing
configuration.

## Duration Options

`min_length_seconds` and `max_gap_seconds` are also evaluated using presentation
timestamps after `lx-ai-core` returns candidate ranges. Candidate ranges are
mapped back to the original frame numbers; no intermediate frame-rate-derived
coordinate is persisted.

Temporal inference requires a complete, finite, strictly increasing timestamp
sequence for the scored frames and an authoritative exclusive boundary after
the final scored frame. Missing or inconsistent timestamps fail with
`TemporalInferenceConfigError`. There is no nominal-frame-rate fallback.

Invalid `temporal_smoothing_enabled` values fail before dispatch with
`TemporalInferenceConfigError`. Accepted values are booleans and common boolean
strings such as `"true"`, `"false"`, `"1"`, `"0"`, `"yes"`, and `"no"`.

## What Disabling Smoothing Does Not Change

Disabling smoothing only affects the timestamp-domain rolling mean applied
before `lx-ai-core`. It does not disable temporal inference, frame scoring,
segment materialization, or prediction segment replacement.

It also does not disable model-specific temporal awareness. For example,
`temporal_model="markov"` can still smooth or diffuse scores through its Markov
post-processing before segment extraction. For raw threshold-like behavior, use
`temporal_model="hysteresis"` with `temporal_smoothing_enabled=false` and
explicit thresholds.

## Persistence

Each temporal inference history stores:

- `raw_temporal_options`: the caller-provided temporal option payload.
- `temporal_options`: normalized timestamp-domain duration options.
- `temporal_options.lx_options`: the exact option map sent to `lx-ai-core`.

This means audit/debug consumers should read `raw_temporal_options` to see what
the caller requested and `temporal_options.lx_options` to see what was executed.

## Maintenance Notes

When changing temporal inference options:

- Keep request keys snake_case.
- Validate invalid option values loudly with `TemporalInferenceConfigError`.
- Keep omitted `temporal_smoothing_enabled` backward compatible with the
  existing one-second smoothing default.
- Keep the `lx-ai-core` frame-count options at their identity values; duration
  behavior belongs to the presentation-timestamp adapter.
- Do not fall back to nominal frame rate when presentation timestamps are
  missing or inconsistent.
- Do not add deployment-wide defaults unless the API contract is intentionally
  expanded.
