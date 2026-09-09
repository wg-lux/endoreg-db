from __future__ import annotations

from math import isnan
from typing import TypeAlias, Any

from django.db import models

NoProductWeightValue: TypeAlias = None
ProductWeightValue: TypeAlias = float | NoProductWeightValue
ProductWeightSource: TypeAlias = str | NoProductWeightValue


class ProductWeight(models.Model):
    objects = models.Manager["ProductWeight"]()

    name: models.CharField[Any, Any] = models.CharField(max_length=255, null=True)
    product: models.ForeignKey[Any] = models.ForeignKey(
        "Product", on_delete=models.CASCADE
    )
    measured: models.FloatField[Any, Any] = models.FloatField(null=True)
    verified: models.FloatField[Any, Any] = models.FloatField(null=True)
    manufacturer: models.FloatField[Any, Any] = models.FloatField(null=True)
    unit: models.ForeignKey[Any] = models.ForeignKey(
        "Unit", on_delete=models.SET_NULL, null=True
    )

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
