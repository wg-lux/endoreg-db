from __future__ import annotations

from typing import TypeVar, cast

from django.core.management.base import BaseCommand, CommandError, CommandParser
from django.db.models import Model, QuerySet

_ModelT = TypeVar("_ModelT", bound=Model)


class BaseVideoCommand(BaseCommand):
    """Shared argument and option helpers for video maintenance commands."""

    def add_video_selection_arguments(
        self,
        parser: CommandParser,
        *,
        limit_help: str = "Maximum number of selected videos to process.",
    ) -> None:
        parser.add_argument(
            "--video-id",
            type=int,
            action="append",
            dest="video_ids",
            help="Restrict processing to one or more VideoFile IDs.",
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=None,
            help=limit_help,
        )

    def add_apply_argument(
        self,
        parser: CommandParser,
        *,
        help_text: str,
    ) -> None:
        parser.add_argument(
            "--apply",
            action="store_true",
            help=help_text,
        )

    def add_json_output_argument(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--json",
            action="store_true",
            dest="json_output",
            help="Emit machine-readable JSON.",
        )

    @staticmethod
    def selected_video_ids_from_options(
        options: dict[str, object],
    ) -> list[int] | None:
        video_ids_option = options.get("video_ids")
        if not isinstance(video_ids_option, list):
            return None
        raw_video_ids = cast(list[object], video_ids_option)
        return [int(str(video_id)) for video_id in raw_video_ids]

    @staticmethod
    def positive_limit_from_options(options: dict[str, object]) -> int | None:
        limit = options.get("limit")
        if limit is None:
            return None
        if not isinstance(limit, int):
            raise CommandError("--limit must be an integer")
        if limit <= 0:
            raise CommandError("--limit must be greater than zero")
        return limit

    @staticmethod
    def apply_video_selection(
        queryset: QuerySet[_ModelT],
        *,
        video_ids: list[int] | None,
        limit: int | None,
    ) -> QuerySet[_ModelT]:
        selected_queryset = queryset
        if video_ids:
            selected_queryset = selected_queryset.filter(pk__in=video_ids)
        if limit is not None:
            return selected_queryset[:limit]
        return selected_queryset
