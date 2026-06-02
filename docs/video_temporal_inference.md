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

When `temporal_smoothing_enabled` is omitted or `true`, endoreg-db converts
`smoothing_window_seconds` to frames using the video FPS and passes that value as
`lx_options["smoothing_window"]`.

Example at 25 fps:

```json
{
  "temporal_smoothing_enabled": true,
  "smoothing_window_seconds": 1.0
}
```

This produces:

```json
{
  "smoothing_window": 25
}
```

When `temporal_smoothing_enabled` is `false`, endoreg-db ignores the requested
window duration for smoothing and passes a one-frame window:

```json
{
  "temporal_smoothing_enabled": false,
  "smoothing_window_seconds": 3.0
}
```

This produces:

```json
{
  "smoothing_window": 1
}
```

`lx-ai-core` treats `smoothing_window=1` as the identity window for frame-score
smoothing. The normalized history payload records
`temporal_smoothing_enabled=false` and `smoothing_window_seconds=0.0` so later
inspection can distinguish "disabled" from a normal one-second smoothing
configuration.

Invalid `temporal_smoothing_enabled` values fail before dispatch with
`TemporalInferenceConfigError`. Accepted values are booleans and common boolean
strings such as `"true"`, `"false"`, `"1"`, `"0"`, `"yes"`, and `"no"`.

## What Disabling Smoothing Does Not Change

Disabling smoothing only affects the rolling smoothing window passed to
`lx-ai-core`. It does not disable temporal inference, frame scoring, segment
materialization, or prediction segment replacement.

It also does not disable model-specific temporal awareness. For example,
`temporal_model="markov"` can still smooth or diffuse scores through its Markov
post-processing before segment extraction. For raw threshold-like behavior, use
`temporal_model="hysteresis"` with `temporal_smoothing_enabled=false` and
explicit thresholds.

## Persistence

Each temporal inference history stores:

- `raw_temporal_options`: the caller-provided temporal option payload.
- `temporal_options`: normalized options, including FPS-derived frame windows.
- `temporal_options.lx_options`: the exact option map sent to `lx-ai-core`.

This means audit/debug consumers should read `raw_temporal_options` to see what
the caller requested and `temporal_options.lx_options` to see what was executed.

## Maintenance Notes

When changing temporal inference options:

- Keep request keys snake_case.
- Validate invalid option values loudly with `TemporalInferenceConfigError`.
- Keep omitted `temporal_smoothing_enabled` backward compatible with the
  existing one-second smoothing default.
- Keep disabled smoothing mapped to `smoothing_window=1`; do not use zero,
  because `lx-ai-core` requires a window of at least one frame.
- Do not add deployment-wide defaults unless the API contract is intentionally
  expanded.
