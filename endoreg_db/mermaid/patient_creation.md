```mermaid
graph TD;
    
    A["Start: Create New Patient (John)"] --> B{"Does the Center Exist?"}
    
    B -- Yes --> C["Retrieve Existing Center ID"]
    B -- No --> D["Create New Medical Center"]
    
    D --> E["Retrieve Newly Created Center ID"]
    C --> F["Save Patient in endoreg_db_patient with Center ID"]
    E --> F
    
    F --> G["Patient Registration Completed"]

