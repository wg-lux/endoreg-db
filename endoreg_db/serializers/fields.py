from __future__ import annotations

from rest_framework import serializers

from endoreg_db.models.administration.center.center import Center


class CenterKeyRelatedField(serializers.SlugRelatedField):
    """
    Canonical machine-facing relation field for Center.

    `center_key` is the stable integration token. Human-readable names stay
    available via separate read-only serializer fields.
    """

    default_error_messages = {
        "does_not_exist": 'Unknown center_key: "{slug_value}"',
        "invalid": "Expected a center_key string.",
    }

    def __init__(self, **kwargs):
        kwargs.setdefault("slug_field", "center_key")
        kwargs.setdefault("queryset", Center.objects.all())
        super().__init__(**kwargs)
