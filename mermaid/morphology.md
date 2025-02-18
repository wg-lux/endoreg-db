```mermaid
graph TD;
    
    A["Start: Select Morphology Classification"] --> B["Store in endoreg_db_findingmorphologyclassification"]
    B --> C["Retrieve Associated Classification Type"]
    C --> D["Save in endoreg_db_patientfinding_morphology"]
    D --> E["Link Morphology to Patient Finding via Foreign Key"]
    E --> F["Morphology Process Completed"]