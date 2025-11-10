"""
Data Loader Helpers and others
"""
from django.core.management import call_command
from pathlib import Path
ASSET_DIR = Path(__file__).resolve().parent.parent / "assets"

def load_default_ai_model(): 
    """
    Loads the default AI model into the database using a predefined checkpoint file.
    
    This function constructs the path to the default AI model checkpoint and invokes the
    Django management command `create_multilabel_model_meta` to register the model.
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
    Loads qualification data into the database using the corresponding Django management command.
    """
    call_command("load_qualification_data", )

def load_shift_data():
    """
    Loads shift data into the database by invoking the corresponding Django management command.
    """
    call_command("load_shift_data", )

def load_base_db_data():
    """
    Loads the base database data by invoking the corresponding Django management command.
    """
    call_command("load_base_db_data", )

def load_information_source():
    """
    Loads information source data into the database by invoking the corresponding Django management command.
    """
    call_command("load_information_source", )

def load_risk_data():
    """
    Loads risk data into the database by invoking the corresponding Django management command.
    """
    call_command("load_risk_data", )

def load_center_data():
    """
    Loads center data into the database by invoking the corresponding Django management command.
    """
    call_command("load_center_data", )

def load_endoscope_data():
    """
    Loads endoscope data into the database by invoking the corresponding Django management command.
    """
    call_command("load_endoscope_data", )

def load_distribution_data():
    """
    Loads distribution data into the database by invoking the corresponding Django management command.
    """
    call_command("load_distribution_data", )

def load_gender_data():
    """
    Loads gender data into the database by invoking the corresponding Django management command.
    """
    call_command("load_gender_data", )

def load_report_reader_flag_data():
    """
    Loads the report reader flag data into the database using the corresponding management command.
    """
    call_command("load_report_reader_flag_data", )
    
def load_pdf_type_data():
    """
    Loads PDF type data into the database by invoking the corresponding Django management command.
    """
    call_command("load_pdf_type_data", )
    
def load_unit_data():
    """
    Loads unit data into the database by invoking the corresponding Django management command.
    """
    call_command("load_unit_data", )
    
def load_disease_data():
    """
    Loads disease data into the database using the corresponding Django management command.
    """
    call_command("load_disease_data", )
    
def load_event_data():
    """
    Loads event data into the database by invoking the corresponding Django management command.
    """
    call_command("load_event_data", )
    
def load_organ_data():
    """
    Loads organ data into the database by invoking the corresponding Django management command.
    """
    call_command("load_organ_data", )
    
def load_contraindication_data():
    """
    Loads contraindication data into the database using the corresponding management command.
    """
    call_command("load_contraindication_data", )
    
def load_examination_data():
    """
    Loads examination data into the database using the corresponding Django management command.
    """
    call_command("load_examination_data", )
    
def load_lab_value_data():
    """
    Loads laboratory value data into the database using the corresponding Django management command.
    """
    call_command("load_lab_value_data", )
    
def load_finding_data():
    """
    Loads finding data into the database by invoking the corresponding Django management command.
    """
    call_command("load_finding_data", )
    
def load_examination_indication_data():
    """
    Loads examination indication data into the database using the corresponding Django management command.
    """
    call_command("load_examination_indication_data", )
    
def load_medication_data():
    """
    Loads medication data into the database using the corresponding Django management command.
    """
    call_command("load_medication_data", )

def load_requirement_data():

    """
    Loads requirement data into the database by invoking the corresponding Django management command.
    """
    call_command("load_requirement_data", )

def load_ai_model_label_data():
    """
    Loads AI model label data into the database by invoking the corresponding Django management command.
    """
    call_command("load_ai_model_label_data", )

def load_ai_model_data():    
    """
    Loads AI model data into the database by invoking the corresponding Django management command.
    """
    call_command("load_ai_model_data", )

def load_green_endoscopy_wuerzburg_data():
    """
    Loads the Green Endoscopy Wuerzburg dataset into the database using the corresponding Django management command.
    """
    call_command("load_green_endoscopy_wuerzburg_data", )

def load_data():
    """
    Loads all predefined data sets into the database in the required sequence.
    
    Calls individual data loading functions in a specific order to ensure that dependencies between data sets are respected.
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

    
    
