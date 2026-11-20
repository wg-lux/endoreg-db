# Sensitivity of file hash checks to pixel changes

## Subject and context

This material describes a deterministic technical validation experiment for
Endoreg-db's SHA-256 file hash checks. It examines whether controlled changes to
synthetic PNG images and their metadata change file hash equality. It is neither
a clinical study nor an estimate of human visual similarity. The smallest pixel
change examined is one intensity level in one color channel of one pixel. No
universal similarity threshold is estimated. Scope and verification evidence are
maintained exclusively under `image_content_hash_pixel_experiment` in
`feature-tracking/CodeQuality.yml`.

## Experimental protocol

Two baseline RGB images with side lengths of 8 and 16 pixels are generated from
`bytes(index % 251 for index in range(side * side * 3))`. Channels are 8-bit
integers. A separate metadata experiment uses a third synthetic 8 × 8 image
from `bytes(range(192))`. No patient data are used.

For each side length, red, green, and blue are changed in separate series. In
generation g, exactly the channel under investigation in the g-th pixel in
row-major order is increased by one; previous changes remain. All baseline values
are at most 250, preventing overflow. Each series contains N = side² forward
generations. The six series yield 3 × (64 + 256) = 960 states.

Pillow encodes each state with `Image.save(format="PNG", pnginfo=None)` and the
defaults of the recorded Pillow version. Decoding then checks exact agreement
with the intended pixel bytes. The file is written through the repository's
atomic wrapper. `get_file_hash` hashes it using the actually installed native
backend. Each series also checks a byte-identical copy under a different filename.

Changes are then reversed in the opposite order. Each reversal must reproduce
the hash of the corresponding earlier state. The final state must also have the
same file bytes as the original. The metadata control pair adds only a PNG text
field with key `experiment` and value `identical pixels, different metadata`.

During the test run, the exporter replaces only the test module's local reference
to `get_file_hash` with an observation wrapper. It calls the real helper and
returns its unchanged result. The files used are immediately archived and checked
again with the same helper. The exporter is therefore not an independent
cryptographic reference implementation. It supplements existing assertions with
an inspectable measurement archive.

## Endpoints and analysis

The primary endpoint is exact equality of the hexadecimal file hash with the
baseline hash of the same series. The test also checks that no earlier hash
recurs within each forward series. Comparisons across different series are not
part of this endpoint.

The export descriptively records the Hamming distance of the 256 digest bits
from the baseline hash. It is calculated as the number of set bits in the bitwise
exclusive OR of both hashes. It is not a measure of visual similarity. No
threshold is fitted to this distance and no theoretical distribution is tested.

Mean squared channel error is calculated over all 3 × N channel values:

`MSE = sum((original_channel - current_channel)²) / (3 × N)`.

For g cumulative one-level changes, MSE = g/(3N). The fraction of changed pixels
is g/N. At the first step, this is 1.5625% for 8 × 8 and 0.390625% for 16 × 16.
The channel change relative to the full 8-bit scale is 1/255.

The 960 states are dependent observations from two baseline images. The 960
reversal steps reproduce previously visited states. Neither these steps nor
repeated test executions count as additional independent image samples.
Consequently, no population confidence intervals, p-values, or general
sensitivity and specificity estimates are reported.

## Results tables and figure captions

Actual measurements are in `measurements.csv`, and the seven experimental series
in `summary.csv`. The full export contains 1,934 observations: 7 baselines,
6 copy controls, 960 forward generations, 960 reversals, and 1 metadata change.
Equivalent files are stored separately for each observation; the file count is
not a count of independent or unique images.

**Figure 1, `example_images`.** Baseline image and first change in the red channel
for both side lengths. Enlargement uses nearest-neighbor interpolation without
smoothing. On the right, a binary mask marks the changed pixel in white; the mask
shows its location, not the actual intensity of the change.

**Figure 2, `hash_distance`.** Hamming distance from the baseline hash against the
number of cumulatively changed pixels, by image size and color channel. Points
are dependent states of a deterministic series, not independent repetitions.

**Figure 3, `controls`.** Proportion of matching baseline hashes for byte-identical
copies, cumulative pixel changes, and a metadata change. The different
denominators are stated directly; the chart is purely descriptive and has no
inferential error bars. The metadata control has zero changed pixels.

All figures are provided as PDF and SVG for vector output and as PNG at 300 dpi.
The original small experimental PNGs are stored without enlargement in `images/`.

## Data dictionary

| Column | Meaning |
| --- | --- |
| series | Pytest case name with channel and side length, or metadata control |
| step | Zero-based order of actually observed hash calls within the series |
| phase | baseline, copy, forward, reverse, or metadata |
| side | Image width and height in pixels |
| changed_pixels | Pixels with at least one channel differing from the baseline |
| changed_channels | Number of channel values differing from the baseline |
| changed_pixels_previous | Pixels differing from the previous observation; baseline 0 |
| max_channel_delta | Maximum absolute channel difference from baseline, in intensity levels |
| mse | Mean squared channel error in squared intensity levels |
| hash_distance_bits | Hamming distance from baseline hash, 0 to 256 bits |
| matches_original | Exact hash equality with the baseline of the same series |
| sha256 | Complete file hash returned by the production helper |
| bytes | PNG file length in bytes |
| image | Relative path to the file actually hashed and archived |

`step` is not the cumulative pixel count: in the pixel series, step 1 is the
unchanged copy and step 2 is the first pixel change.

## Interpretation and limitations

The smallest pixel change examined and distinguished is one intensity level in
one channel of one pixel. Only the first forward step of each series differs
from the original at exactly one pixel. Later positions were not individually
tested against the unchanged original. This does not establish a threshold for
arbitrary images, positions, formats, encoders, or scaling operations.

A metadata change can change the file hash even when all pixels remain identical.
Thus, no exclusively pixel-based threshold fully describes file hash equality.
SHA-256 checks message or file integrity [1]. In addition to losslessly encoded
pixel values, PNG permits supplementary text information [2]. Pillow supports
such information through `pnginfo` [3].

The experiment confirms expected implementation behavior and claims no new
cryptographic property. It does not examine perceptual hashing, JPEG re-encoding,
clinical images, or human perception. Observing no collision is not proof of
general collision freedom. Test duration is not a hash performance benchmark
because it includes encoding, assertions, and archiving.

## Reproduction and archive structure

From the repository root in the configured project environment:

```sh
.devenv/state/venv/bin/pyright
.devenv/state/venv/bin/python -m scripts.collect_image_hash_publication
```

The exporter uses the canonical `get_runtime_paths().export_dir` and creates a
new directory with a timestamp and unique identifier. Pytest continues to use
its own isolated runtime paths. Existing exports are not overwritten. Required
Nix libraries and Binutils must be on the shell search path; thanks to `no_db`,
the test case needs no database. The run must finish with seven passing tests.
A failed test run terminates the export with an error.

| File or directory | Contents |
| --- | --- |
| PROTOKOLL.md | This protocol, including data dictionary and references |
| images/ | All 1,934 PNG files actually hashed |
| measurements.csv | One row per hash observation |
| summary.csv | Summary of the seven series |
| figures/ | Three figures, each as PDF, SVG, and PNG |
| code/ | Selected exact source snapshots and repository license |
| environment.yml | Versions, commit, uncommitted-change indication, and native backend hash |
| pytest.log | Output of the experimental test execution |
| checksums.yml | SHA-256 checksums of archive files, excluding the checksum list itself |

The directory is also exported as a ZIP with a separate SHA-256 checksum.
Checksums support integrity checks; they are not digital signatures. With
`dirty_worktree: true`, the recorded commit alone does not describe all executed
sources. Included snapshots document selected relevant files; the complete
repository, all transitive dependencies, and native binary are not included.
This does not claim an identical run in an independently rebuilt environment.

## References

1. National Institute of Standards and Technology. *Secure Hash Standard (SHS)*.
   FIPS PUB 180-4, August 2015. DOI: [10.6028/NIST.FIPS.180-4](https://doi.org/10.6028/NIST.FIPS.180-4).
   [Official publication page](https://csrc.nist.gov/pubs/fips/180-4/upd1/final).
2. World Wide Web Consortium. *Portable Network Graphics (PNG) Specification, Third Edition*.
   W3C Recommendation, June 24, 2025.
   [Versioned specification](https://www.w3.org/TR/2025/REC-png-3-20250624/).
3. Pillow contributors. *Image file formats: PNG*. Online documentation,
   [PNG save options](https://pillow.readthedocs.io/en/stable/handbook/image-file-formats.html#png).
   The package version actually used is recorded in `environment.yml`; the online
   documentation may describe a newer version.

Sources accessed: October 16, 2026. Code and protocol were created with AI assistance;
scientific interpretation and final publication approval remain with the authors.
