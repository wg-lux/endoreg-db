'''Module for product models'''
from .product import Product
from .product_material import ProductMaterial
from .product_group import ProductGroup
from .reference_product import ReferenceProduct
from .product_weight import ProductWeight

__all__ = [
    'Product',
    'ProductMaterial',
    'ProductGroup',
    'ReferenceProduct',
    'ProductWeight',
]
