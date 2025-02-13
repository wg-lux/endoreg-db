```mermaid
graph TD;

  

%% Step 1: Image Classification (Start)

A[**Image Classification**] -->|Uses| B[**Label**];

  

%% Step 2: Video Segmentation

A -->|Segments Stored In| C[**Video Segmentation**];

  

%% Step 3: Automatic Segmentation Detection

D["**find_segments_in_prediction_array()**"] -->|Detects & Segments| C;

  

%% Step 4: Fetching Unclassified Frames for Annotation

E["get_legacy_binary_classification_annotation_tasks_by_label()"] -->|Finds Frames Needing Annotation| F;

  

%% Step 5: Binary Classification Task Management

F[**Binary Classification Task**] -->|Assigned To| G[**Frame**];

F -->|Assigned To| H[**Legacy Frame**];

F -->|Uses| B;

%% Step 6: Task Completion & Cleanup (End)

I["clear_finished_legacy_tasks()"] -->|Removes Completed Tasks| F;

  

%% Example Data

X1(["Example: Frame classified as 'Polyp Present'"]) -.-> A;

X2(["Example: Video segment marked from 100s to 200s"]) -.-> C;

X3(["Example: 10 unclassified frames found"]) -.-> F;