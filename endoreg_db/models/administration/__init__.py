# AI
from .ai import (
    AiModel,
    ActiveModel,
    ModelType,
)

# Case
from .case import (
    Case,
    CaseTemplate,
    CaseTemplateRule,
    CaseTemplateRuleType,
    CaseTemplateRuleValue,
    CaseTemplateRuleValueType,
    CaseTemplateType,
)

# Center
from .center import (
    Center,
    CenterProduct,
    CenterResource,
    CenterWaste,
    CenterShift,
)


#TODO Review module
# from .permissions import () 

# Person
from .person import (
    Person,
    Examiner, 
    Patient, 
    PortalUserInfo,     
    FirstName,
    LastName,
    Profession,
    Employee,
    EmployeeType,
    EmployeeQualification,
)

# Product
from .product import (
    Product,
    ProductMaterial,
    ProductGroup,
    ReferenceProduct,
    ProductWeight,
)

from .qualification import (
    Qualification,
    QualificationType,
)

from .shift import (
    Shift,
    ShiftType,
    ScheduledDays,
)

__all__ = [
    # AI
    "AiModel",
    "ActiveModel",
    "ModelType",

    # Case
    "Case",
    "CaseTemplate",
    "CaseTemplateRule",
    "CaseTemplateRuleType",
    "CaseTemplateRuleValue",
    "CaseTemplateRuleValueType",
    "CaseTemplateType",

    # Center
    "Center",
    "CenterProduct",
    "CenterResource",
    "CenterWaste",
    "CenterShift",

    # Person
    "Person",
    "Patient",
    "Examiner",
    "PortalUserInfo",
    "FirstName",
    "LastName",
    "Profession",
    "Employee",
    "EmployeeType",
    "EmployeeQualification",

    # Product
    'Product',
    'ProductMaterial',
    'ProductGroup',
    'ReferenceProduct',
    'ProductWeight',

    # Qualification
    "Qualification",
    "QualificationType",

    # Shift
    "Shift",
    "ShiftType",
    "ScheduledDays",
]

