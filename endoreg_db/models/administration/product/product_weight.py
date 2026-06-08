from __future__ import annotations

from math import isnan
from types import NoneType
from typing import TYPE_CHECKING, TypeAlias

from django.db import models

if TYPE_CHECKING:
    from ...other.unit import Unit
    from .product import Product
    from .product_group import ProductGroup

NoProductWeightValue: TypeAlias = NoneType
ProductWeightValue: TypeAlias = float | NoProductWeightValue
ProductWeightSource: TypeAlias = str | NoProductWeightValue


class ProductWeightManager(models.Manager["ProductWeight"]):
    def get_by_natural_key(
        self,
        product: "Product",
        product_group: "ProductGroup",
        weight: float,
    ) -> "ProductWeight":
        return self.get(product=product, product_group=product_group, weight=weight)


class ProductWeight(models.Model):
    objects = ProductWeightManager()

    name: models.CharField[str | NoProductWeightValue, str | NoProductWeightValue]
    name = models.CharField(max_length=255, null=True)
    product: models.ForeignKey[Product, Product] = models.ForeignKey(
        "Product", on_delete=models.CASCADE
    )
    measured: models.FloatField[
        ProductWeightValue, ProductWeightValue
    ] = models.FloatField(null=True)
    verified: models.FloatField[
        ProductWeightValue, ProductWeightValue
    ] = models.FloatField(null=True)
    manufacturer: models.FloatField[
        ProductWeightValue, ProductWeightValue
    ] = models.FloatField(null=True)
    unit: models.ForeignKey[
        Unit | NoProductWeightValue,
        Unit | NoProductWeightValue,
    ] = models.ForeignKey("Unit", on_delete=models.SET_NULL, null=True)

    if TYPE_CHECKING:
        pass

    @staticmethod
    def _has_weight(value: ProductWeightValue) -> bool:
        if value is None:
            return False
        return not isnan(value)

    def get_weight(self) -> ProductWeightValue:
        if self._has_weight(self.verified):
            return self.verified
        if self._has_weight(self.measured):
            return self.measured
        if self._has_weight(self.manufacturer):
            return self.manufacturer
        return None

    def get_weight_source(self) -> ProductWeightSource:
        if self._has_weight(self.verified):
            return "verified"
        if self._has_weight(self.measured):
            return "measured"
        if self._has_weight(self.manufacturer):
            return "manufacturer"
        return None

    def __str__(self) -> str:
        return f"{self.product} - {self.get_weight()} {self.unit} (Source: {self.get_weight_source()})"
