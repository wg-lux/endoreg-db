```mermaid
graph TD;
    
    A["Start: Select Finding Location Classification"] --> B["Store in endoreg_db_findinglocationclassification"]
    B --> C["Retrieve Available Choices"]
    C --> D["Save Selected Location in endoreg_db_patientfinding_locations"]
    D --> E["Link Location to Patient Finding via Foreign Key"]
    E --> F["Finding Location Process Completed"]