```mermaid
graph TD;
    
    A["Start: Select Intervention"] --> B["Store in endoreg_db_findingintervention"]
    B --> C["Retrieve Patient Finding ID"]
    C --> D["Save in endoreg_db_patientfindingintervention"]
    D --> E["Link Intervention to Patient Finding via Foreign Key"]
    E --> F["Intervention Process Completed"]
