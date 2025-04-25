from django.core.management.base import BaseCommand
from endoreg_db.models import (
    EmissionFactor,
    Resource,
    Waste,
    Material,
    Product,
    ProductGroup,
    ReferenceProduct,
    TransportRoute,
    CenterWaste,
    CenterResource,
    ProductMaterial,
    ProductWeight,

    # Other models for ForeignKeys
    Unit, Center
)
from collections import OrderedDict

from ...utils import load_model_data_from_yaml
from ...data import (
    EMISSION_FACTOR_DATA_DIR,
    RESOURCE_DATA_DIR,
    WASTE_DATA_DIR,
    MATERIAL_DATA_DIR,
    PRODUCT_DATA_DIR,
    PRODUCT_GROUP_DATA_DIR,
    REFERENCE_PRODUCT_DATA_DIR,
    TRANSPORT_ROUTE_DATA_DIR,
    CENTER_WASTE_DATA_DIR,
    CENTER_RESOURCE_DATA_DIR,
    PRODUCT_MATERIAL_DATA_DIR,
    PRODUCT_WEIGHT_DATA_DIR,
)

IMPORT_METADATA = OrderedDict({
    EmissionFactor.__name__: {
        "dir": EMISSION_FACTOR_DATA_DIR,
        "model": EmissionFactor, 
        "foreign_keys": ["unit"], 
        "foreign_key_models": [Unit] 
    },
    Resource.__name__: {
        "dir": RESOURCE_DATA_DIR,
        "model": Resource,
        "foreign_keys": [],
        "foreign_key_models": []
    },
    Waste.__name__: {
        "dir": WASTE_DATA_DIR,
        "model": Waste,
        "foreign_keys": [],
        "foreign_key_models": []
    },
    Material.__name__: {
        "dir": MATERIAL_DATA_DIR,
        "model": Material,
        "foreign_keys": ["emission_factor"],
        "foreign_key_models": [EmissionFactor]
    },
    ProductGroup.__name__: {
        "dir": PRODUCT_GROUP_DATA_DIR,
        "model": ProductGroup,
        "foreign_keys": [],
        "foreign_key_models": []
    },
    TransportRoute.__name__: {
        "dir": TRANSPORT_ROUTE_DATA_DIR,
        "model": TransportRoute,
        "foreign_keys": ["emission_factor", "unit"],
        "foreign_key_models": [EmissionFactor, Unit]
    },
    Product.__name__: {
        "dir": PRODUCT_DATA_DIR,
        "model": Product,
        "foreign_keys": ["product_group", "transport_route"],
        "foreign_key_models": [ProductGroup, TransportRoute]
    },
    ReferenceProduct.__name__: {
        "dir": REFERENCE_PRODUCT_DATA_DIR,
        "model": ReferenceProduct,
        "foreign_keys": ["product","product_group"],
        "foreign_key_models": [Product,ProductGroup]
    },
    CenterWaste.__name__: {
        "dir": CENTER_WASTE_DATA_DIR,
        "model": CenterWaste,
        "foreign_keys": ["waste", "center", "unit", "emission_factor"],
        "foreign_key_models": [Waste, Center, Unit, EmissionFactor]
    },
    CenterResource.__name__: {
        "dir": CENTER_RESOURCE_DATA_DIR,
        "model": CenterResource,
        "foreign_keys": ["center","resource", "unit", "transport_emission_factor", "use_emission_factor"],
        "foreign_key_models": [Center,Resource, Unit, EmissionFactor, EmissionFactor]
    },
    ProductMaterial.__name__: {
        "dir": PRODUCT_MATERIAL_DATA_DIR,
        "model": ProductMaterial,
        "foreign_keys": ["product", "material", "unit"],
        "foreign_key_models": [Product, Material, Unit]
    },
    ProductWeight.__name__: {
        "dir": PRODUCT_WEIGHT_DATA_DIR,
        "model": ProductWeight,
        "foreign_keys": ["product", "unit"],
        "foreign_key_models": [Product, Unit]
    }
})

class Command(BaseCommand):
    help = """Load all .yaml files in the data/intervention directory
    into the Intervention and InterventionType model"""

    def add_arguments(self, parser):
        parser.add_argument(
            '--verbose',
            action='store_true',
            help='Display verbose output',
        )

    def handle(self, *args, **options):
        verbose = options['verbose']
        for model_name in IMPORT_METADATA.keys():
            _metadata = IMPORT_METADATA[model_name]
            load_model_data_from_yaml(
                self,
                model_name,
                _metadata,
                verbose
            )