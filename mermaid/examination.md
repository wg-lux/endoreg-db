```mermaid
graph TD;
    
    A["Start: Record Examination"] --> B["Store in endoreg_db_examination"]
    B --> C["Retrieve Patient ID"]
    C --> D["Save in endoreg_db_patientexamination with Patient ID"]
    D --> E["Link Patient to Examination via Foreign Key"]
    E --> F["Examination Process Completed"]
