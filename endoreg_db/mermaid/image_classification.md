```mermaid
graph TD;

    %% Image Classification Process
    A[**Image Classification Annotation**] -->|Attached to| B[**Frame**];
    A -->|Attached to| C[**Legacy Frame**];
    A -->|Attached to| D[**Legacy Image**];
    A -->|Uses Predefined| E[**Label**];

    %% Video Segmentation Process
    F[**Abstract Label Video Segment**] -->|Base Class for| G[**Legacy Label Video Segment**];
    F -->|Base Class for| H[**Label Video Segment**];

    %% Video References
    G -->|Segmentation of| I[**Legacy Video**];
    H -->|Segmentation of| J[**Modern Video**];

    %% Metadata & Prediction Handling
    G -->|Uses Prediction Data| K[**Legacy Video Prediction Meta**];
    H -->|Uses Prediction Data| L[**Video Prediction Meta**];

    %% Automatic Segmentation Process
    M["**find_segments_in_prediction_array()**"] -->|Detects & Segments Frames| F;

    %% Example Data
    X1(["**Example: Frame classified as 'Polyp Present'**"]) -.-> A;
    X2(["**Example: Segment starts at frame 100, ends at frame 200**"]) -.-> F;
    X3(["**Example: Legacy Video used for segmentation**"]) -.-> G;
