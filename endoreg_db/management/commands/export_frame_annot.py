from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from black import output
from django.core.management.base import BaseCommand, CommandError

from endoreg_db.export.frames.export_frames_with_labels import (
    annotation_exporter_client,
    export_config,
    export_job_failed_error,
)

# Usage Example:
# python manage.py export_frame_annot \
#   --output-path data/export/frames.csv \
#   --video-id 1 \
#   --only-true \
#   --transcode-frames \
#   --transcode-fps 50


class Command(BaseCommand):
    help = "Export frame annotations to CSV with optional transcoding. /n Usage Example: /n python manage.py export_frame_annot \
   --output-path data/export/frames.csv \
   --video-id 1 \
   --only-true \
   --transcode-frames \
   --transcode-fps 50"

    def add_arguments(self, parser):
        parser.add_argument(
            "--config",
            type=str,
            help="Path to YAML config for export settings.",
        )
        parser.add_argument(
            "--output-path",
            type=str,
            help="CSV output path (required if --config is not provided).",
        )
        parser.add_argument("--video-id", type=int, help="Filter by video id.")
        parser.add_argument("--label-id", type=int, help="Filter by label id.")
        parser.add_argument(
            "--information-source-name",
            type=str,
            help="Filter by information source name.",
        )
        only_true_group = parser.add_mutually_exclusive_group()
        only_true_group.add_argument(
            "--only-true",
            action="store_true",
            default=None,
            help="Only export rows with value=True.",
        )
        only_true_group.add_argument(
            "--only-false",
            action="store_false",
            dest="only_true",
            default=None,
            help="Only export rows with value=False.",
        )
        parser.add_argument("--limit", type=int, help="Limit number of rows.")

        load_base_group = parser.add_mutually_exclusive_group()
        load_base_group.add_argument(
            "--load-base-data",
            action="store_true",
            default=None,
            help="Load base data before export.",
        )
        load_base_group.add_argument(
            "--no-load-base-data",
            action="store_false",
            dest="load_base_data",
            default=None,
            help="Do not load base data before export.",
        )

        transcode_group = parser.add_mutually_exclusive_group()
        transcode_group.add_argument(
            "--transcode-frames",
            action="store_true",
            default=None,
            help="Extract frames to frame_dir at configured FPS.",
        )
        transcode_group.add_argument(
            "--no-transcode-frames",
            action="store_false",
            dest="transcode_frames",
            default=None,
            help="Disable frame extraction even if YAML enables it.",
        )
        parser.add_argument("--transcode-fps", type=float, help="Transcode FPS.")
        parser.add_argument(
            "--transcode-quality",
            type=int,
            help="FFmpeg quality (lower is higher quality).",
        )
        parser.add_argument(
            "--transcode-ext",
            type=str,
            help="Output frame extension (e.g. jpg).",
        )
        overwrite_group = parser.add_mutually_exclusive_group()
        overwrite_group.add_argument(
            "--transcode-overwrite",
            action="store_true",
            default=None,
            help="Overwrite existing frames.",
        )
        overwrite_group.add_argument(
            "--no-transcode-overwrite",
            action="store_false",
            dest="transcode_overwrite",
            default=None,
            help="Do not overwrite existing frames.",
        )

        frame_path_group = parser.add_mutually_exclusive_group()
        frame_path_group.add_argument(
            "--use-frame-pk-paths",
            action="store_true",
            default=None,
            help="Write frame_relative_path using frame pk names.",
        )
        frame_path_group.add_argument(
            "--no-use-frame-pk-paths",
            action="store_false",
            dest="use_frame_pk_paths",
            default=None,
            help="Use stored relative_path values for frame_relative_path.",
        )

    def handle(self, *args, **options):
        config = self._build_config(options)
        client = annotation_exporter_client()
        try:
            result = client.run_export(config)
        except export_job_failed_error as exc:
            raise CommandError(str(exc)) from exc

        self.stdout.write(
            self.style.SUCCESS(
                "Exported %s rows to %s"
                % (result.row_count, result.output_path)
            )
        )

    def _build_config(self, options) -> export_config:
        config_path = options.get("config")
        if config_path:
            config = export_config.from_yaml(Path(config_path))
        else:
            output_path = options.get("output_path")
            if not output_path:
                output_path = "data/export/frames.csv"
            config = export_config(output_path=Path(output_path))

        updates = {}
        for key in (
            "output_path",
            "video_id",
            "label_id",
            "information_source_name",
            "only_true",
            "limit",
            "load_base_data",
            "transcode_frames",
            "transcode_fps",
            "transcode_quality",
            "transcode_ext",
            "transcode_overwrite",
            "use_frame_pk_paths",
        ):
            value = options.get(key)
            if value is not None:
                updates[key] = value

        if updates:
            config = replace(config, **updates)

        if not config.output_path:
            raise CommandError("output_path is required.")

        return config