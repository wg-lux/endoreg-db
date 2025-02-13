```mermaid
graph TD;
    
    A["Start: Identify Medical Finding"] --> B["Store in endoreg_db_finding"]
    B --> C["Retrieve Patient Examination ID"]
    C --> D["Save in endoreg_db_patientfinding with Examination ID (Link Finding to Examination via Foreign Key)"]
    D --> E["Finding Process Completed"]
