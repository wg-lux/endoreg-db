"""
Data Loader Helpers and others
"""
from django.core.management import call_command


def load_information_source():
    """Load Information Source Data"""
    call_command("load_information_source", )

def load_risk_data():
    """Load Risk Data"""
    call_command("load_risk_data", )

def load_center_data():
    """Load Center Data"""
    call_command("load_center_data", )

def load_endoscope_data():
    """Load Endoscope Data"""
    call_command("load_endoscope_data", )

def load_distribution_data():
    """Load Distribution Data"""
    call_command("load_distribution_data", )

def load_gender_data():
    """Load Gender Data"""
    call_command("load_gender_data", )

def load_report_reader_flag_data():
    """Load Report Reader Flag Data"""
    call_command("load_report_reader_flag_data", )
    
def load_pdf_type_data():
    """Load PDF Type Data"""
    call_command("load_pdf_type_data", )
    
def load_unit_data():
    call_command("load_unit_data", )
    
def load_disease_data():
    call_command("load_disease_data", )
    
def load_event_data():
    call_command("load_event_data", )
    
def load_organ_data():
    call_command("load_organ_data", )
    
def load_contraindication_data():
    call_command("load_contraindication_data", )
    
def load_examination_data():
    call_command("load_examination_data", )
    
def load_lab_value_data():
    call_command("load_lab_value_data", )
    
def load_finding_data():
    call_command("load_finding_data", )
    
def load_examination_indication_data():
    call_command("load_examination_indication_data", )
    
def load_medication_data():
    call_command("load_medication_data", )

def load_requirement_data():
    call_command("load_requirement_data", )

def load_ai_model_label_data():
    call_command("load_ai_model_label_data", )

def load_ai_model_data():    
    call_command("load_ai_model_data", )


