from __future__ import annotations
from typing import TYPE_CHECKING, Any

from django.db import models
from endoreg_db.models.utils import NameNaturalKeyManager

if TYPE_CHECKING:
    from ...administration import ReferenceProduct


# get debug from settings
# from django.conf import settings
# DEBUG = settings.DEBUG


class EmissionFactor(models.Model):
    """
    Represents an emission factor with associated unit and value.

    Attributes:
        name (str): The name of the emission factor.
        unit (ForeignKey): The unit associated with the emission factor.
        value (float): The value of the emission factor.
    """

    objects = NameNaturalKeyManager["EmissionFactor"]()

    name: models.CharField[Any, Any] = models.CharField(max_length=255)
    # German display name; NULL when the catalogue carries none.
    name_de: models.CharField[Any, Any] = models.CharField(
        max_length=255, blank=True, null=True
    )
    unit: models.ForeignKey[Any] = models.ForeignKey(
        "Unit", on_delete=models.SET_NULL, null=True
    )
    value: models.FloatField[Any, Any] = models.FloatField()

    if TYPE_CHECKING:
        pass

        @property
        def reference_products(self) -> models.QuerySet["ReferenceProduct"]: ...

        @property
        def reference_product_package(self) -> models.QuerySet["ReferenceProduct"]: ...

        @property
        def reference_product_product(self) -> models.QuerySet["ReferenceProduct"]: ...

    def natural_key(self) -> tuple[str]:
        """
        Returns the natural key for the emission factor.

        Returns:
            tuple: The natural key consisting of the name.
        """
        return (str(self.name),)

    def __str__(self, verbose: bool = False) -> str:
        """
        String representation of the emission factor.

        Args:
            verbose (bool): If True, includes additional details.

        Returns:
            str: The string representation of the emission factor.
        """
        result = f"{self.name}:	{self.value} per {self.unit}"
        if verbose:
            result += "\n\tSources:"
            for source in self.sources():
                result += f"\n\t\t{source}"

        return result

    def get_reference_products(self) -> list["ReferenceProduct"]:
        """
        Retrieves all reference products associated with the emission factor.

        Returns:
            list: A list of ReferenceProduct instances associated with this emission factor.
        """
        from ...administration.product import ReferenceProduct

        reference_products: list["ReferenceProduct"] = []
        reference_products += ReferenceProduct.objects.filter(
            emission_factor_total=self
        )
        reference_products += ReferenceProduct.objects.filter(
            emission_factor_package=self
        )
        reference_products += ReferenceProduct.objects.filter(
            emission_factor_product=self
        )

        return reference_products

    def sources(self) -> list["ReferenceProduct"]:
        """
        Retrieves all sources related to the emission factor.

        Returns:
            list: A list of sources related to the emission factor.
        """
        sources: list["ReferenceProduct"] = []
        sources.extend(self.get_reference_products())
        return sources
