from __future__ import annotations

import json
from io import StringIO

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from endoreg_db.models import (
    Center,
    Frame,
    ImageClassificationAnnotation,
    InformationSource,
    Label,
    LabelVideoSegment,
    VideoFile,
)


class ReconcileFrameSegmentAnnotationsCommandTest(TestCase):
    def setUp(self):
        self.center = Center.objects.create(name="reconcile-command-center")
        self.video = VideoFile.objects.create(
            center=self.center,
            video_hash="reconcile-command-video",
            original_file_name="reconcile_command.mp4",
            fps=25.0,
            frame_count=4,
        )
        self.frames = [
            Frame.objects.create(
                video=self.video,
                frame_number=frame_number,
                relative_path=f"frame_{frame_number:07d}.jpg",
                is_extracted=True,
            )
            for frame_number in range(4)
        ]
        self.manual_source = InformationSource.objects.create(name="manual_annotation")
        self.prediction_source = InformationSource.objects.create(name="prediction")
        self.label = Label.objects.create(name="reconcile-command-label")

    def _manual_segment(self, *, start: int = 0, end: int = 2):
        return LabelVideoSegment.objects.create(
            video_file=self.video,
            label=self.label,
            source=self.manual_source,
            start_frame_number=start,
            end_frame_number=end,
        )

    def test_json_dry_run_is_default_and_makes_no_writes(self):
        self._manual_segment()

        output = StringIO()
        call_command(
            "reconcile_frame_segment_annotations",
            "--video-id",
            str(self.video.pk),
            "--json",
            stdout=output,
        )

        payload = json.loads(output.getvalue())
        self.assertTrue(payload["spec"]["dry_run"])
        self.assertEqual(payload["summary"]["missing_annotations"], 2)
        self.assertEqual(ImageClassificationAnnotation.objects.count(), 0)

    def test_apply_repairs_missing_annotations(self):
        self._manual_segment()

        output = StringIO()
        call_command(
            "reconcile_frame_segment_annotations",
            "--video-id",
            str(self.video.pk),
            "--apply",
            stdout=output,
        )

        self.assertIn("created=2", output.getvalue())
        self.assertEqual(ImageClassificationAnnotation.objects.count(), 2)

    def test_track_option_scopes_reconciliation(self):
        self._manual_segment(start=0, end=2)
        LabelVideoSegment.objects.create(
            video_file=self.video,
            label=self.label,
            source=self.prediction_source,
            start_frame_number=2,
            end_frame_number=4,
        )

        output = StringIO()
        call_command(
            "reconcile_frame_segment_annotations",
            "--video-id",
            str(self.video.pk),
            "--track",
            "manual",
            "--apply",
            "--json",
            stdout=output,
        )

        payload = json.loads(output.getvalue())
        self.assertEqual(payload["summary"]["eligible_segments"], 1)
        self.assertEqual(payload["summary"]["created_annotations"], 2)
        self.assertEqual(
            ImageClassificationAnnotation.objects.filter(
                information_source=self.manual_source
            ).count(),
            2,
        )
        self.assertEqual(
            ImageClassificationAnnotation.objects.filter(
                information_source=self.prediction_source
            ).count(),
            0,
        )

    def test_apply_requires_explicit_scope(self):
        with self.assertRaises(CommandError):
            call_command("reconcile_frame_segment_annotations", "--apply")
