"""Explicit reference-catalog model bindings; YAML cannot import model classes."""

from django.db import models
from lx_dtypes.models.contracts.reference_catalog import ReferenceKind

from endoreg_db.models.administration.ai.ai_model import AiModel
from endoreg_db.models.administration.center.center import Center
from endoreg_db.models.medical.contraindication import Contraindication
from endoreg_db.models.other.distribution.date_value_distribution import (
    DateValueDistribution,
)
from endoreg_db.models.medical.disease import Disease
from endoreg_db.models.medical.disease import DiseaseClassification
from endoreg_db.models.medical.disease import DiseaseClassificationChoice
from endoreg_db.models.medical.hardware.endoscope import Endoscope
from endoreg_db.models.medical.hardware.endoscope import EndoscopeType
from endoreg_db.models.medical.hardware.endoscopy_processor import EndoscopyProcessor
from endoreg_db.models.medical.event import Event
from endoreg_db.models.medical.examination.examination import Examination
from endoreg_db.models.medical.examination.examination_indication import (
    ExaminationIndication,
)
from endoreg_db.models.medical.examination.examination_indication import (
    ExaminationIndicationClassification,
)
from endoreg_db.models.medical.examination.examination_indication import (
    ExaminationIndicationClassificationChoice,
)
from endoreg_db.models.medical.examination.examination_time import ExaminationTime
from endoreg_db.models.medical.examination.examination_time_type import (
    ExaminationTimeType,
)
from endoreg_db.models.medical.examination.examination_type import ExaminationType
from endoreg_db.models.medical.finding.finding import Finding
from endoreg_db.models.medical.finding.finding_classification import (
    FindingClassification,
)
from endoreg_db.models.medical.finding.finding_classification import (
    FindingClassificationChoice,
)
from endoreg_db.models.medical.finding.finding_classification import (
    FindingClassificationType,
)
from endoreg_db.models.medical.finding.finding_intervention import FindingIntervention
from endoreg_db.models.medical.finding.finding_intervention import (
    FindingInterventionType,
)
from endoreg_db.models.medical.finding.finding_type import FindingType
from endoreg_db.models.other.gender import Gender
from endoreg_db.models.other.information_source import InformationSource
from endoreg_db.models.other.information_source import InformationSourceType
from endoreg_db.models.medical.laboratory.lab_value import LabValue
from endoreg_db.models.label.label import Label
from endoreg_db.models.label.label_set import LabelSet
from endoreg_db.models.label.label_type import LabelType
from endoreg_db.models.medical.medication.medication import Medication
from endoreg_db.models.medical.medication.medication_indication import (
    MedicationIndication,
)
from endoreg_db.models.medical.medication.medication_indication_type import (
    MedicationIndicationType,
)
from endoreg_db.models.medical.medication.medication_intake_time import (
    MedicationIntakeTime,
)
from endoreg_db.models.medical.medication.medication_schedule import MedicationSchedule
from endoreg_db.models.administration.ai.model_type import ModelType
from endoreg_db.models.other.distribution.multiple_categorical_value_distribution import (
    MultipleCategoricalValueDistribution,
)
from endoreg_db.models.other.distribution.numeric_value_distribution import (
    NumericValueDistribution,
)
from endoreg_db.models.medical.organ import Organ
from endoreg_db.models.medical.patient.patient_lab_sample import PatientLabSampleType
from endoreg_db.models.metadata.pdf_meta import PdfType
from endoreg_db.models.media.pdf.report_reader.report_reader_flag import (
    ReportReaderFlag,
)
from endoreg_db.models.medical.risk.risk import Risk
from endoreg_db.models.medical.risk.risk_type import RiskType
from endoreg_db.models.other.distribution.single_categorical_value_distribution import (
    SingleCategoricalValueDistribution,
)
from endoreg_db.models.other.tag import Tag
from endoreg_db.models.other.unit import Unit
from endoreg_db.models.label.video_segmentation_label import VideoSegmentationLabel
from endoreg_db.models.label.video_segmentation_labelset import (
    VideoSegmentationLabelSet,
)


from endoreg_db.models.administration.center.center_resource import CenterResource
from endoreg_db.models.administration.center.center_waste import CenterWaste
from endoreg_db.models.other.emission.emission_factor import EmissionFactor
from endoreg_db.models.other.material import Material
from endoreg_db.models.administration.product.product import Product
from endoreg_db.models.administration.product.product_group import ProductGroup
from endoreg_db.models.administration.product.product_material import ProductMaterial
from endoreg_db.models.administration.product.product_weight import ProductWeight
from endoreg_db.models.administration.person.profession import Profession
from endoreg_db.models.administration.qualification.qualification import Qualification
from endoreg_db.models.administration.qualification.qualification_type import (
    QualificationType,
)
from endoreg_db.models.administration.product.reference_product import ReferenceProduct
from endoreg_db.models.other.resource import Resource
from endoreg_db.models.administration.shift.shift import Shift
from endoreg_db.models.administration.shift.shift_type import ShiftType
from endoreg_db.models.other.transport_route import TransportRoute
from endoreg_db.models.other.waste import Waste

CATALOG_MODELS: dict[ReferenceKind, type[models.Model]] = {
    ReferenceKind.AI_MODEL: AiModel,
    ReferenceKind.CENTER: Center,
    ReferenceKind.CONTRAINDICATION: Contraindication,
    ReferenceKind.DATE_VALUE_DISTRIBUTION: DateValueDistribution,
    ReferenceKind.DISEASE: Disease,
    ReferenceKind.DISEASE_CLASSIFICATION: DiseaseClassification,
    ReferenceKind.DISEASE_CLASSIFICATION_CHOICE: DiseaseClassificationChoice,
    ReferenceKind.ENDOSCOPE: Endoscope,
    ReferenceKind.ENDOSCOPE_TYPE: EndoscopeType,
    ReferenceKind.ENDOSCOPY_PROCESSOR: EndoscopyProcessor,
    ReferenceKind.EVENT: Event,
    ReferenceKind.EXAMINATION: Examination,
    ReferenceKind.EXAMINATION_INDICATION: ExaminationIndication,
    ReferenceKind.EXAMINATION_INDICATION_CLASSIFICATION: ExaminationIndicationClassification,
    ReferenceKind.EXAMINATION_INDICATION_CLASSIFICATION_CHOICE: ExaminationIndicationClassificationChoice,
    ReferenceKind.EXAMINATION_TIME: ExaminationTime,
    ReferenceKind.EXAMINATION_TIME_TYPE: ExaminationTimeType,
    ReferenceKind.EXAMINATION_TYPE: ExaminationType,
    ReferenceKind.FINDING: Finding,
    ReferenceKind.FINDING_CLASSIFICATION: FindingClassification,
    ReferenceKind.FINDING_CLASSIFICATION_CHOICE: FindingClassificationChoice,
    ReferenceKind.FINDING_CLASSIFICATION_TYPE: FindingClassificationType,
    ReferenceKind.FINDING_INTERVENTION: FindingIntervention,
    ReferenceKind.FINDING_INTERVENTION_TYPE: FindingInterventionType,
    ReferenceKind.FINDING_TYPE: FindingType,
    ReferenceKind.GENDER: Gender,
    ReferenceKind.INFORMATION_SOURCE: InformationSource,
    ReferenceKind.INFORMATION_SOURCE_TYPE: InformationSourceType,
    ReferenceKind.LAB_VALUE: LabValue,
    ReferenceKind.LABEL: Label,
    ReferenceKind.LABEL_SET: LabelSet,
    ReferenceKind.LABEL_TYPE: LabelType,
    ReferenceKind.MEDICATION: Medication,
    ReferenceKind.MEDICATION_INDICATION: MedicationIndication,
    ReferenceKind.MEDICATION_INDICATION_TYPE: MedicationIndicationType,
    ReferenceKind.MEDICATION_INTAKE_TIME: MedicationIntakeTime,
    ReferenceKind.MEDICATION_SCHEDULE: MedicationSchedule,
    ReferenceKind.MODEL_TYPE: ModelType,
    ReferenceKind.MULTIPLE_CATEGORICAL_VALUE_DISTRIBUTION: MultipleCategoricalValueDistribution,
    ReferenceKind.NUMERIC_VALUE_DISTRIBUTION: NumericValueDistribution,
    ReferenceKind.ORGAN: Organ,
    ReferenceKind.PATIENT_LAB_SAMPLE_TYPE: PatientLabSampleType,
    ReferenceKind.PDF_TYPE: PdfType,
    ReferenceKind.REPORT_READER_FLAG: ReportReaderFlag,
    ReferenceKind.RISK: Risk,
    ReferenceKind.RISK_TYPE: RiskType,
    ReferenceKind.SINGLE_CATEGORICAL_VALUE_DISTRIBUTION: SingleCategoricalValueDistribution,
    ReferenceKind.TAG: Tag,
    ReferenceKind.UNIT: Unit,
    ReferenceKind.VIDEO_SEGMENTATION_LABEL: VideoSegmentationLabel,
    ReferenceKind.VIDEO_SEGMENTATION_LABEL_SET: VideoSegmentationLabelSet,
    ReferenceKind.CENTER_RESOURCE: CenterResource,
    ReferenceKind.CENTER_WASTE: CenterWaste,
    ReferenceKind.EMISSION_FACTOR: EmissionFactor,
    ReferenceKind.MATERIAL: Material,
    ReferenceKind.PRODUCT: Product,
    ReferenceKind.PRODUCT_GROUP: ProductGroup,
    ReferenceKind.PRODUCT_MATERIAL: ProductMaterial,
    ReferenceKind.PRODUCT_WEIGHT: ProductWeight,
    ReferenceKind.PROFESSION: Profession,
    ReferenceKind.QUALIFICATION: Qualification,
    ReferenceKind.QUALIFICATION_TYPE: QualificationType,
    ReferenceKind.REFERENCE_PRODUCT: ReferenceProduct,
    ReferenceKind.RESOURCE: Resource,
    ReferenceKind.SHIFT: Shift,
    ReferenceKind.SHIFT_TYPE: ShiftType,
    ReferenceKind.TRANSPORT_ROUTE: TransportRoute,
    ReferenceKind.WASTE: Waste,
}
