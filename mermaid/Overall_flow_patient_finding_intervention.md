```mermaid
graph TD;

    %% Main Patient Workflow
    A[Patient: John] --> B[Examination: Colonoscopy]
    B --> C[Finding: Colon Polyp]
    C --> D[Finding Location: Colonoscopy Default - Right Flexure]
    D --> E[Finding Morphology: Colon Lesion Planarity Default - Colon Lesion Planarity Excavated]
    E --> F[Intervention: Colon Lesion Polypectomy - Hot Snare]
    F --> G[Report Generation]
