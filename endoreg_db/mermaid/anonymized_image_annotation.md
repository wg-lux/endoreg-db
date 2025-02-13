```mermaid
graph TD;

    %% File Handling Process
    A[**Uploaded File**] -->|Original File Uploaded| B[**Anonymized File**]
    B -->|File Anonymized & Stored| C[**Anonymous Image Annotation**]

    %% Image Annotation & Processing
    C -->|Label Assigned| D[**Anonymized Image Label**]
    C -->|Detected Personal Info Removed| E[**Dropped Name**]

    %% Relationships & Processing
    E -->|Stored for Record-Keeping| C;
    D -->|Predefined Labels Used| C;

    %% Example Data (Corrected & Meaningful)
    X1(["**Example: Uploaded colonoscopy.jpg**"]) -.-> A;
    X2(["**Example: anonymized_colonoscopy.jpg**"]) -.-> B;
    X3(["**Example: Label - 'Polyp Detected'**"]) -.-> D;
    X4(["**Example: Name 'John Doe' removed**"]) -.-> E;
