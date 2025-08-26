from ast import List
import pickle
from endoreg_db.models import Requirement, Examination, PatientExamination
from endoreg_db.serializers import PatientExaminationSerializer, ExaminationSerializer, FindingClassificationSerializer, RequirementSerializer


class LookupService:
    def __init__(self, db_session):
        self.db_session = db_session
        self.hexdict = int()

    def hash_lookup(self, key, value):
        """
        Store a key-value pair in the lookup dictionary.
        """
        lookup_dict = pickle.loads(bytes.fromhex(self.hexdict))
        lookup_dict[key] = value
        self.hexdict = pickle.dumps(dict).hex()
        return True
    def hash_retrieve(self, key):
        """
        Retrieve a value by key from the lookup dictionary.
        """
        lookup_dict = pickle.loads(bytes.fromhex(self.hexdict))
        return lookup_dict.get(key, None)
    
    def hash_add_key_value(self, key, value):
        """
        Add a key-value pair to the lookup dictionary.
        """
        lookup_dict = pickle.loads(bytes.fromhex(self.hexdict))
        lookup_dict[key] = value
        self.hexdict = pickle.dumps(lookup_dict).hex()
        return True
    
    def hash_dehash(self):
        """
        Retrieve the entire lookup dictionary.
        """
        lookup_dict = pickle.loads(bytes.fromhex(self.hexdict))
        return lookup_dict
    
    def fetch_examination_dict(self, patient_id, tags: List[str] = None):
        """
        Fetch examinations for a given patient ID, optionally filtered by tags.
        """
        query = self.db_session.query(Examination).filter(Examination.patient_id == patient_id)
        if tags:
            query = query.filter(Examination.tags.any(Examination.tags.in_(tags)))
        examinations = query.all()
        return [ExaminationSerializer(exam).data for exam in examinations]  
    
    def fetch_patient_examination_by_id(self, patient_id, examination_id):
        """
        Fetch a patient examination object from the database by its ID.
        """
        return self.db_session.query(PatientExamination).filter(PatientExamination.id == patient_examination_id).first()
    
    def fetch_patient_examinations(self, patient_id):
        """
        Fetch all patient examinations for a given patient ID.
        """
        examinations = self.db_session.query(PatientExamination).filter(PatientExamination.patient_id == patient_id).all()
        return [PatientExaminationSerializer(exam).data for exam in examinations]

    def fetch_findings_by_patient_examination(self, patient_examination_id):
        """
        Fetch findings associated with a given patient examination ID.
        """
        patient_examination = self.db_session.query(PatientExamination).filter(PatientExamination.id == patient_examination_id).first()
        if not patient_examination:
            return []
        return [finding.serialize() for finding in patient_examination.findings]
    
    def fetch_classifications_by_finding(self, finding_id):
        """
        Fetch classifications associated with a given finding ID.
        """
        finding = self.db_session.query(Finding).filter(Finding.id == finding_id).first()
        if not finding:
            return []
        return [classification.serialize() for classification in finding.classifications]
    
    def fetch_default_finding_classification(self):
        """
        Fetch the default finding classification from the database.
        """
        return self.db_session.query(FindingClassification).filter(FindingClassification.is_default == True).first()
    
    def fetch_finding_classification_choice_by_requirement(self, requirement_id):
        """
        Retrieve finding classification choices based on the provided requirement ID.
        """
        requirement = self.get_requirement_by_id(requirement_id)
        if not requirement:
            return []
        return [choice.serialize() for choice in requirement.finding_classification_choices]
    
    def fetch_requirement_choices_by_requirement(self, requirement_id):
        """
        Retrieve requirement choices based on the provided requirement ID.
        """
        requirement = self.get_requirement_by_id(requirement_id)
        if not requirement:
            return []
        return [choice.serialize() for choice in requirement.requirement_choices]
    
    def available_finding_classification_choices_by_finding_classification(self, finding_classification):
        """
        Retrieve available finding classification choices based on the provided finding classification.
        """
        return self.db_session.query(FindingClassificationChoice).filter(
            FindingClassificationChoice.finding_classification_id == finding_classification.id
        ).all()

    def get_requirement_by_id(self, requirement_id):
        """
        Fetch a requirement object from the database by its ID.
        """
        return self.db_session.query(Requirement).filter(Requirement.id == requirement_id).first()

    def get_all_requirements_for_requirement_sets(self, requirement_set_ids: List[int]):
        """
        Fetch all requirements associated with the given requirement set IDs.
        """
        return self.db_session.query(Requirement).filter(Requirement.requirement_set_id.in_(requirement_set_ids)).all()
    
    def get_all_requirement_sets_for_examination(self, examination_id):
        """
        Fetch all requirement sets associated with the given examination ID.
        """
        examination = self.db_session.query(Examination).filter(Examination.id == examination_id).first()
        if not examination:
            return []
        return [req_set.serialize() for req_set in examination.requirement_sets]

    def serialize_requirement(self, requirement):
        """
        Serialize a requirement object to a byte stream.
        """
        return pickle.dumps(requirement)

    def deserialize_requirement(self, serialized_requirement):
        """
        Deserialize a byte stream back to a requirement object.
        """
        return pickle.loads(serialized_requirement)