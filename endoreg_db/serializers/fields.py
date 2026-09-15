from __future__ import annotations

from rest_framework import serializers

from endoreg_db.models.administration.center.center import Center


class CenterKeyRelatedField(serializers.SlugRelatedField[Center]):  # pyright: ignore[reportInvalidTypeArguments]
    """
    Canonical machine-facing relation field for Center.

    `center_key` is the stable integration token. Human-readable names stay
    available via separate read-only serializer fields.
    """

    default_error_messages = {
        "does_not_exist": 'Unknown center_key: "{slug_value}"',
        "invalid": "Expected a center_key string.",
    }

    def __init__(
        self,
        *,
        source: str | None = None,
        required: bool | None = None,
        allow_null: bool = False,
        read_only: bool = False,
    ) -> None:
        serializers.SlugRelatedField.__init__(  # pyright: ignore[reportUnknownMemberType]
            self,
            slug_field="center_key",
            queryset=Center.objects.all(),
            source=source or "",
            required=bool(required),
            allow_null=allow_null,
            read_only=read_only,
        )
