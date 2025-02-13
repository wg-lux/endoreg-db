```mermaid
graph TD;

    %% Label Management
    A[**Video Segmentation Label**] -->|Used in| B[**Video Segmentation Annotation**];

    %% Annotation Process
    B -->|Assigned to| C[**Raw Video File**];

    %% Attributes Breakdown
    A -->|Has Fields| A1[**Name, Description, Color, Priority**];
    B -->|Has Fields| B1[**Start Time, Stop Time, Validity**];
    
    %% Example Data (Placed Outside for Clarity)
    X1(["Example: Label - 'Polyp Detected'"]) -.-> A;
    X2(["Example: Video1.mp4, Start: 10s, Stop: 15s"]) -.-> B;

