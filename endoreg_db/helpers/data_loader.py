"""
Data Loader Helpers and others
"""
from django.core.management import call_command
from pathlib import Path
ASSET_DIR = Path(__file__).resolve().parent.parent / "assets"

def load_default_ai_model(): 
    """
    Register the project's default AI model in the database using the bundled checkpoint file.
    """
    model_path = f"{ASSET_DIR.as_posix()}/colo_segmentation_RegNetX800MF_6.safetensors"
    # Pass arguments individually to call_command
    call_command(
        "create_multilabel_model_meta",
        "--model_path",
        model_path
    )

def load_qualification_data():
    """
    Load predefined qualification records into the database.
    
    Populates the application's qualification reference data required for correct operation by invoking the project's data-loading mechanism.
    """
    call_command("load_qualification_data", )

def load_shift_data():
    """
    Load predefined shift records into the application's database.
    
    This populates the reference data for work shifts required by other application components.
    """
    call_command("load_shift_data", )

def load_base_db_data():
    """
    Load foundational reference data required by the application.
    
    Populates the base database with core fixtures used across the project.
    """
    call_command("load_base_db_data", )

def load_information_source():
    """
    Load predefined information source records into the database.
    
    Populates the database with the application's information source dataset.
    """
    call_command("load_information_source", )

def load_risk_data():
    """
    Load the predefined risk dataset into the database.
    """
    call_command("load_risk_data", )

def load_center_data():
    """
    Load the predefined center dataset into the application's database.
    
    This populates the database with the standard set of center records required by the application.
    """
    call_command("load_center_data", )

def load_endoscope_data():
    """
    Load predefined endoscope records into the application's database.
    
    Populates the database with default endoscope entries required by the application.
    """
    call_command("load_endoscope_data", )

def load_distribution_data():
    """
    Load predefined distribution reference data into the database.
    
    Populates the project's distribution lookup/seed entries required by application features.
    """
    call_command("load_distribution_data", )

def load_gender_data():
    """
    Load predefined gender records into the database.
    """
    call_command("load_gender_data", )

def load_report_reader_flag_data():
    """
    Load the predefined report reader flag dataset into the application's database.
    
    This populates the set of report reader flags required by the application.
    """
    call_command("load_report_reader_flag_data", )
    
def load_pdf_type_data():
    """
    Load predefined PDF type records into the database.
    
    Ensures the application's required PDF type entries are present by executing the project's PDF type data loader.
    """
    call_command("load_pdf_type_data", )
    
def load_unit_data():
    """
    Populate the application's measurement/unit reference data in the database.
    
    This ensures the standard unit lookup entries required by domain models are present.
    """
    call_command("load_unit_data", )
    
def load_disease_data():
    """
    Load predefined disease fixtures into the application's database.
    
    Invokes the Django management command `load_disease_data` to populate disease-related records.
    """
    call_command("load_disease_data", )
    
def load_event_data():
    """
    Load predefined event records into the database.
    
    Ensures the application's event dataset is populated so dependent data and lookups are available.
    """
    call_command("load_event_data", )
    
def load_organ_data():
    """
    Load predefined organ reference data into the database.
    
    Populates the application's organ-related reference records by executing the corresponding Django management command.
    """
    call_command("load_organ_data", )
    
def load_contraindication_data():
    """
    Load contraindication records into the application's database.
    
    Uses the project's management command to insert the predefined contraindication fixtures required by the application.
    """
    call_command("load_contraindication_data", )
    
def load_examination_data():
    """
    Load predefined examination records into the database.
    
    Populates examination-related lookup and seed data required by the application.
    """
    call_command("load_examination_data", )
    
def load_lab_value_data():
    """
    Load predefined laboratory value records into the database.
    
    Populates the database with the project's standard laboratory value reference data.
    """
    call_command("load_lab_value_data", )
    
def load_finding_data():
    """
    Load predefined finding records into the application's database.
    
    Populates the database with the standard set of finding entries required by the application.
    """
    call_command("load_finding_data", )
    
def load_examination_indication_data():
    """
    Populate the database with predefined examination indication records.
    """
    call_command("load_examination_indication_data", )
    
def load_medication_data():
    """
    Populate medication reference records used by the application.
    """
    call_command("load_medication_data", )

def load_requirement_data():

    """
    Loads requirement data into the database by invoking the corresponding Django management command.
    """
    call_command("load_requirement_data", )

def load_ai_model_label_data():
    """
    Load predefined AI model label records into the application's database.
    
    Invokes the Django management command that inserts the standard set of AI model labels used by the application.
    """
    call_command("load_ai_model_label_data", )

def load_ai_model_data():    
    """
    Populate the database with predefined AI model records required by the application.
    
    This function triggers the Django management command "load_ai_model_data" to perform the import.
    """
    call_command("load_ai_model_data", )

def load_green_endoscopy_wuerzburg_data():
    """
    Load the Green Endoscopy Wuerzburg dataset into the database.
    
    Invokes the Django management command that imports and persists Green Endoscopy Wuerzburg data.
    """
    call_command("load_green_endoscopy_wuerzburg_data", )

def load_data():
    """
    Load all predefined dataset fixtures into the database in the order required by their dependencies.
    
    Calls the module-level loader functions in a fixed sequence so records that other datasets depend on are created before their dependents.
    """
    
    load_information_source()
    load_risk_data()
    load_center_data()
    load_endoscope_data()
    load_distribution_data()

    load_gender_data()
    load_report_reader_flag_data()
    load_pdf_type_data()
    load_unit_data()
    load_disease_data()
    load_event_data()
    load_organ_data()
    load_contraindication_data()
    load_examination_data()
    load_lab_value_data()
    load_finding_data()
    load_examination_indication_data()
    load_medication_data()
    load_requirement_data()

    load_ai_model_label_data()
    load_ai_model_data()

    load_green_endoscopy_wuerzburg_data()

    
    