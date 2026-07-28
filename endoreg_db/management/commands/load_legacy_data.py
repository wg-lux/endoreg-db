# endoreg_db/management/commands/load_legacy_data.py

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from types import TracebackType
from typing import Protocol, TypeAlias, TypedDict, Unpack, cast

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError, CommandParser
from django.db import transaction
from django.db.models import Max
from lx_dtypes.models.contracts.legacy_data_import import (
    LegacyDataImportCommandOptionsPayload,
    LegacyImageImportRowPayload,
    LegacyImportManifestPayload,
    LegacyIntOrNull,
    LegacyTextOrNull,
    NullValue,
    dump_legacy_import_manifest,
)
from pydantic import ValidationError

from endoreg_db.models.administration.center.center import Center
from endoreg_db.models.aidataset.aidataset import AIDataSet
from endoreg_db.models.label.annotation.image_classification import (
    ImageClassificationAnnotation,
)
from endoreg_db.models.label.label import Label
from endoreg_db.models.label.label_set import LabelSet
from endoreg_db.models.media.frame.frame import Frame
from endoreg_db.models.media.video.video_file import VideoFile
from endoreg_db.utils.file_operations import (
    atomic_copy_file,
    atomic_write_file,
    ensure_directory,
)
from endoreg_db.utils.paths import (
    EndoregPathsModel,
    ensure_within_protected_root,
)

DEFAULT_LABELSET_NAME = (
    "multilabel_classification_colonoscopy_default"  # must be present in the DB
)
DEFAULT_LABELSET_VERSION = 1

VideoFileOrNull: TypeAlias = VideoFile | NullValue
AiDataSetOrNull: TypeAlias = AIDataSet | NullValue
ExceptionTypeOrNull: TypeAlias = type[BaseException] | NullValue
ExceptionValueOrNull: TypeAlias = BaseException | NullValue
TracebackOrNull: TypeAlias = TracebackType | NullValue


class LegacyDataImportCommandOptions(TypedDict):
    jsonl_path: str
    images_root: str
    video_id: LegacyIntOrNull
    center_id: LegacyIntOrNull
    dataset_name: str
    dataset_description: str
    labelset_name: str
    labelset_version: int
    dry_run: bool
    staged_images_root: str
    manifest_path: str


class PersistedRecord(Protocol):
    id: int
    pk: LegacyIntOrNull


class NamedPersistedRecord(PersistedRecord, Protocol):
    name: str


class LabelSetRecord(NamedPersistedRecord, Protocol):
    version: int


class LegacyVideoRecord(PersistedRecord, Protocol):
    center_id: int
    center: Center
    video_hash: str
    frame_dir: LegacyTextOrNull

    def save(self, *, update_fields: list[str]) -> None: ...


class AnnotationCollection(Protocol):
    def add(self, annotation: ImageClassificationAnnotation) -> None: ...


@dataclass
class LegacyImportState:
    frame_number_counters: dict[int | str, int] = field(
        default_factory=dict[int | str, int]
    )
    legacy_videos_by_old_examination_id: dict[str, VideoFile] = field(
        default_factory=dict[str, VideoFile]
    )
    legacy_video_ids_by_old_examination_id: dict[str, LegacyIntOrNull] = field(
        default_factory=dict[str, LegacyIntOrNull]
    )
    staged_dirs_by_video_key: dict[int | str, Path] = field(
        default_factory=dict[int | str, Path]
    )
    used_video_ids: set[int] = field(default_factory=set[int])
    frame_count: int = 0
    annotation_count: int = 0
    copied_image_count: int = 0
    missing_image_count: int = 0


def _persisted_record(
    model: Center | VideoFile | LabelSet | AIDataSet,
) -> PersistedRecord:
    return cast(PersistedRecord, model)


def _named_record(model: LabelSet | AIDataSet) -> NamedPersistedRecord:
    return cast(NamedPersistedRecord, model)


def _labelset_record(labelset: LabelSet) -> LabelSetRecord:
    return cast(LabelSetRecord, labelset)


def _legacy_video_record(video: VideoFile) -> LegacyVideoRecord:
    return cast(LegacyVideoRecord, video)


class Command(BaseCommand):
    help = (
        "Import legacy multilabel image data from JSONL + images into the database.\n"
        "- Backfills legacy old_examination_id values into synthetic VideoFile rows\n"
        "- Creates Frames linked through Frame.video_id only\n"
        "- Creates ImageClassificationAnnotations (value=True) for each listed label\n"
        "- Reuses/extends an existing LabelSet\n"
        "- Fills an AIDataSet (image dataset) with all annotations via image_annotations"
    )

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--jsonl-path",
            type=str,
            default=str(
                Path(settings.BASE_DIR)
                / "data"
                / "legacy_data"
                / "legacy_img_dicts.jsonl"
            ),
            help="Path to legacy_img_dicts.jsonl",
        )
        parser.add_argument(
            "--images-root",
            type=str,
            default=str(Path(settings.BASE_DIR) / "data" / "legacy_data" / "images"),
            help="Root directory containing legacy images.",
        )
        parser.add_argument(
            "--video-id",
            type=int,
            required=False,
            default=None,
            help=(
                "Fallback VideoFile for rows without old_examination_id. "
                "Also supplies the Center used for synthetic legacy VideoFile rows."
            ),
        )
        parser.add_argument(
            "--center-id",
            type=int,
            required=False,
            default=None,
            help=(
                "Center used for synthetic legacy VideoFile rows when --video-id "
                "is not provided."
            ),
        )
        parser.add_argument(
            "--dataset-name",
            type=str,
            default="legacy_multilabel_dataset_v1",  # later change this if needed
            help="Name for the created/reused AIDataSet.",
        )
        parser.add_argument(
            "--dataset-description",
            type=str,
            default="Legacy multilabel colonoscopy dataset imported from JSONL.",
            help="Description for the created AIDataSet.",
        )
        parser.add_argument(
            "--labelset-name",
            type=str,
            default=DEFAULT_LABELSET_NAME,
            help="LabelSet name to use (must exist).",
        )
        parser.add_argument(
            "--labelset-version",
            type=int,
            default=DEFAULT_LABELSET_VERSION,
            help="LabelSet version to use (must exist).",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Parse and validate, but do not write anything to the database.",
        )
        parser.add_argument(
            "--staged-images-root",
            type=str,
            default="",
            help="Protected staging directory for copied legacy images.",
        )
        parser.add_argument(
            "--manifest-path",
            type=str,
            default="",
            help="Manifest path under the protected migration manifest tier.",
        )

    def handle(
        self,
        *args: str,
        **options: Unpack[LegacyDataImportCommandOptions],
    ) -> None:
        command_options = self._validate_command_options(options)
        jsonl_path = Path(command_options.jsonl_path)
        images_root = Path(command_options.images_root)
        self._validate_source_paths(jsonl_path=jsonl_path, images_root=images_root)
        fallback_video, center = self._resolve_import_context(
            video_id=command_options.video_id,
            center_id=command_options.center_id,
        )
        import_key = self._import_key(fallback_video=fallback_video, center=center)
        staged_images_root = self._resolve_staged_images_root(
            raw_path=command_options.staged_images_root,
            import_key=import_key,
        )
        manifest_path = self._resolve_manifest_path(
            raw_path=command_options.manifest_path,
            import_key=import_key,
        )
        if not command_options.dry_run:
            ensure_directory(staged_images_root)
        self._report_import_context(fallback_video=fallback_video, center=center)
        labelset = self._get_existing_labelset(
            labelset_name=command_options.labelset_name,
            labelset_version=command_options.labelset_version,
        )
        self._report_labelset(labelset)
        ai_dataset = self._prepare_ai_dataset(command_options)
        self._run_import(
            command_options=command_options,
            jsonl_path=jsonl_path,
            images_root=images_root,
            staged_images_root=staged_images_root,
            manifest_path=manifest_path,
            fallback_video=fallback_video,
            center=center,
            labelset=labelset,
            ai_dataset=ai_dataset,
        )

    # ------------------------------------------------------------------
    # Helper methods
    # ------------------------------------------------------------------

    def _validate_command_options(
        self, options: LegacyDataImportCommandOptions
    ) -> LegacyDataImportCommandOptionsPayload:
        option_keys = LegacyDataImportCommandOptions.__annotations__.keys()
        return LegacyDataImportCommandOptionsPayload.model_validate(
            {key: options[key] for key in option_keys}
        )

    def _validate_source_paths(self, *, jsonl_path: Path, images_root: Path) -> None:
        if not jsonl_path.exists():
            raise CommandError(f"JSONL file not found: {jsonl_path}")
        if not images_root.exists():
            raise CommandError(f"Images root directory not found: {images_root}")

    def _import_key(self, *, fallback_video: VideoFileOrNull, center: Center) -> str:
        if fallback_video is not None:
            return f"video_{_persisted_record(fallback_video).id}"
        return f"center_{_persisted_record(center).id}"

    def _report_import_context(
        self, *, fallback_video: VideoFileOrNull, center: Center
    ) -> None:
        if fallback_video is not None:
            self.stdout.write(
                self.style.NOTICE(
                    f"Using VideoFile id={_persisted_record(fallback_video).id} "
                    "only for rows without old_examination_id."
                )
            )
        self.stdout.write(
            self.style.NOTICE(
                f"Using Center id={_persisted_record(center).id} "
                "for synthetic legacy VideoFile rows."
            )
        )

    def _report_labelset(self, labelset: LabelSet) -> None:
        labelset_record = _labelset_record(labelset)
        self.stdout.write(
            self.style.SUCCESS(
                f"Using LabelSet '{labelset_record.name}' "
                f"(version={labelset_record.version}, id={labelset_record.id})."
            )
        )

    def _prepare_ai_dataset(
        self, command_options: LegacyDataImportCommandOptionsPayload
    ) -> AiDataSetOrNull:
        if command_options.dry_run:
            self.stdout.write(
                self.style.WARNING("Dry run: AIDataSet will NOT be created.")
            )
            return None

        ai_dataset, created = AIDataSet.objects.get_or_create(
            name=command_options.dataset_name,
            defaults={
                "description": command_options.dataset_description,
                "dataset_type": AIDataSet.DATASET_TYPE_IMAGE,
                "ai_model_type": AIDataSet.AI_MODEL_TYPE_IMAGE_MULTILABEL,
                "is_active": True,
            },
        )
        self._report_ai_dataset(ai_dataset=ai_dataset, created=created)
        return ai_dataset

    def _report_ai_dataset(self, *, ai_dataset: AIDataSet, created: bool) -> None:
        dataset_record = _named_record(ai_dataset)
        if created:
            self.stdout.write(
                self.style.SUCCESS(
                    f"Created AIDataSet id={dataset_record.id}, "
                    f"name='{dataset_record.name}'."
                )
            )
            return

        current_count = ai_dataset.get_annotations_queryset().count()
        self.stdout.write(
            self.style.WARNING(
                f"Re-using existing AIDataSet id={dataset_record.id}, "
                f"name='{dataset_record.name}'. "
                f"(Current annotation_count={current_count})"
            )
        )

    def _run_import(
        self,
        *,
        command_options: LegacyDataImportCommandOptionsPayload,
        jsonl_path: Path,
        images_root: Path,
        staged_images_root: Path,
        manifest_path: Path,
        fallback_video: VideoFileOrNull,
        center: Center,
        labelset: LabelSet,
        ai_dataset: AiDataSetOrNull,
    ) -> None:
        state = LegacyImportState()
        context = (
            transaction.atomic if not command_options.dry_run else self._noop_context
        )
        with context():
            self._import_rows(
                jsonl_path=jsonl_path,
                images_root=images_root,
                staged_images_root=staged_images_root,
                fallback_video=fallback_video,
                center=center,
                labelset=labelset,
                ai_dataset=ai_dataset,
                dry_run=command_options.dry_run,
                state=state,
            )
            self._finish_import(
                command_options=command_options,
                jsonl_path=jsonl_path,
                images_root=images_root,
                staged_images_root=staged_images_root,
                manifest_path=manifest_path,
                center=center,
                ai_dataset=ai_dataset,
                state=state,
            )

    def _import_rows(
        self,
        *,
        jsonl_path: Path,
        images_root: Path,
        staged_images_root: Path,
        fallback_video: VideoFileOrNull,
        center: Center,
        labelset: LabelSet,
        ai_dataset: AiDataSetOrNull,
        dry_run: bool,
        state: LegacyImportState,
    ) -> None:
        with jsonl_path.open("r", encoding="utf-8") as source:
            for line_num, raw_line in enumerate(source, start=1):
                line = raw_line.strip()
                if not line:
                    continue
                item = self._parse_legacy_row(
                    line=line,
                    line_num=line_num,
                    jsonl_path=jsonl_path,
                )
                self._import_row(
                    item=item,
                    line_num=line_num,
                    images_root=images_root,
                    staged_images_root=staged_images_root,
                    fallback_video=fallback_video,
                    center=center,
                    labelset=labelset,
                    ai_dataset=ai_dataset,
                    dry_run=dry_run,
                    state=state,
                )

    def _parse_legacy_row(
        self, *, line: str, line_num: int, jsonl_path: Path
    ) -> LegacyImageImportRowPayload:
        try:
            return LegacyImageImportRowPayload.model_validate_json(line)
        except ValidationError as exc:
            raise CommandError(
                f"Invalid JSON on line {line_num} of {jsonl_path}: {exc}"
            ) from exc

    def _import_row(
        self,
        *,
        item: LegacyImageImportRowPayload,
        line_num: int,
        images_root: Path,
        staged_images_root: Path,
        fallback_video: VideoFileOrNull,
        center: Center,
        labelset: LabelSet,
        ai_dataset: AiDataSetOrNull,
        dry_run: bool,
        state: LegacyImportState,
    ) -> None:
        legacy_examination_id = item.normalized_old_examination_id()
        target_video = self._resolve_video_for_legacy_item(
            old_examination_id=legacy_examination_id,
            center=center,
            fallback_video=fallback_video,
            dry_run=dry_run,
            cache=state.legacy_videos_by_old_examination_id,
        )
        self._record_used_video(
            target_video=target_video,
            legacy_examination_id=legacy_examination_id,
            state=state,
        )
        video_staged_root = self._ensure_video_frame_dir(
            target_video=target_video,
            staged_images_root=staged_images_root,
            staged_dirs_by_video_key=state.staged_dirs_by_video_key,
            dry_run=dry_run,
        )
        self._stage_legacy_image(
            images_root=images_root,
            video_staged_root=video_staged_root,
            filename=item.filename,
            line_num=line_num,
            dry_run=dry_run,
            state=state,
        )
        frame = self._create_legacy_frame(
            target_video=target_video,
            filename=item.filename,
            dry_run=dry_run,
            state=state,
        )
        self._create_legacy_annotations(
            frame=frame,
            label_names=item.labels,
            labelset=labelset,
            ai_dataset=ai_dataset,
            dry_run=dry_run,
            state=state,
        )

    def _record_used_video(
        self,
        *,
        target_video: VideoFile,
        legacy_examination_id: LegacyTextOrNull,
        state: LegacyImportState,
    ) -> None:
        target_video_record = _legacy_video_record(target_video)
        if legacy_examination_id is not None:
            state.legacy_video_ids_by_old_examination_id[legacy_examination_id] = (
                target_video_record.pk
            )
        if target_video_record.pk is not None:
            state.used_video_ids.add(target_video_record.pk)

    def _stage_legacy_image(
        self,
        *,
        images_root: Path,
        video_staged_root: Path,
        filename: str,
        line_num: int,
        dry_run: bool,
        state: LegacyImportState,
    ) -> None:
        image_path, staged_image_path = self._resolve_legacy_image_paths(
            images_root=images_root,
            video_staged_root=video_staged_root,
            filename=filename,
            line_num=line_num,
        )
        if not image_path.exists():
            state.missing_image_count += 1
            self.stdout.write(
                self.style.WARNING(
                    f"Image file does not exist for line {line_num}: {image_path}"
                )
            )
            return
        if dry_run or staged_image_path.exists():
            return
        atomic_copy_file(
            source=image_path,
            destination=staged_image_path,
            preserve_metadata=True,
        )
        state.copied_image_count += 1

    def _create_legacy_frame(
        self,
        *,
        target_video: VideoFile,
        filename: str,
        dry_run: bool,
        state: LegacyImportState,
    ) -> Frame:
        state.frame_count += 1
        frame = Frame(
            video=target_video,
            frame_number=self._next_frame_number(
                target_video=target_video,
                counters=state.frame_number_counters,
            ),
            relative_path=filename,
            timestamp=None,
            is_extracted=True,
        )
        if not dry_run:
            frame.save()
        return frame

    def _create_legacy_annotations(
        self,
        *,
        frame: Frame,
        label_names: list[str],
        labelset: LabelSet,
        ai_dataset: AiDataSetOrNull,
        dry_run: bool,
        state: LegacyImportState,
    ) -> None:
        for label_name in label_names:
            label = self._get_or_create_label_and_attach_to_labelset(
                label_name=label_name,
                labelset=labelset,
                dry_run=dry_run,
            )
            state.annotation_count += 1
            annotation = ImageClassificationAnnotation(
                frame=frame,
                label=label,
                value=True,
                annotator="legacy_import",
            )
            self._save_annotation(
                annotation=annotation,
                ai_dataset=ai_dataset,
                dry_run=dry_run,
            )

    def _save_annotation(
        self,
        *,
        annotation: ImageClassificationAnnotation,
        ai_dataset: AiDataSetOrNull,
        dry_run: bool,
    ) -> None:
        if dry_run:
            return
        annotation.save()
        if ai_dataset is not None:
            annotations = cast(
                AnnotationCollection,
                ai_dataset.get_annotations_queryset(),
            )
            annotations.add(annotation)

    def _finish_import(
        self,
        *,
        command_options: LegacyDataImportCommandOptionsPayload,
        jsonl_path: Path,
        images_root: Path,
        staged_images_root: Path,
        manifest_path: Path,
        center: Center,
        ai_dataset: AiDataSetOrNull,
        state: LegacyImportState,
    ) -> None:
        if command_options.dry_run:
            self.stdout.write(
                self.style.WARNING(
                    f"[DRY RUN] Processed {state.frame_count} Frames, "
                    f"{state.annotation_count} Annotations. "
                    "No database changes were committed."
                )
            )
            return
        if ai_dataset is None:
            raise CommandError("AIDataSet is required outside dry-run mode.")
        self._report_completed_import(ai_dataset=ai_dataset, state=state)
        self._write_manifest(
            command_options=command_options,
            jsonl_path=jsonl_path,
            images_root=images_root,
            staged_images_root=staged_images_root,
            manifest_path=manifest_path,
            center=center,
            state=state,
        )

    def _report_completed_import(
        self, *, ai_dataset: AIDataSet, state: LegacyImportState
    ) -> None:
        self.stdout.write(
            self.style.SUCCESS(
                f"Imported {state.frame_count} Frames, {state.annotation_count} "
                "ImageClassificationAnnotations into "
                f"AIDataSet id={_persisted_record(ai_dataset).id}."
            )
        )

    def _write_manifest(
        self,
        *,
        command_options: LegacyDataImportCommandOptionsPayload,
        jsonl_path: Path,
        images_root: Path,
        staged_images_root: Path,
        manifest_path: Path,
        center: Center,
        state: LegacyImportState,
    ) -> None:
        manifest = dump_legacy_import_manifest(
            LegacyImportManifestPayload(
                command="load_legacy_data",
                created_at=datetime.now(timezone.utc).isoformat(),
                jsonl_path=str(jsonl_path.resolve()),
                images_root=str(images_root.resolve()),
                staged_images_root=str(staged_images_root),
                fallback_video_id=command_options.video_id,
                center_id=_persisted_record(center).id,
                used_video_ids=sorted(state.used_video_ids),
                legacy_video_ids_by_old_examination_id=(
                    state.legacy_video_ids_by_old_examination_id
                ),
                dataset_name=command_options.dataset_name,
                frame_count=state.frame_count,
                annotation_count=state.annotation_count,
                copied_image_count=state.copied_image_count,
                missing_image_count=state.missing_image_count,
            )
        )
        atomic_write_file(
            destination=manifest_path,
            content=[json.dumps(manifest, indent=2, sort_keys=True).encode("utf-8")],
        )
        self.stdout.write(f"Manifest written to {manifest_path}")

    def _get_existing_labelset(
        self, labelset_name: str, labelset_version: int
    ) -> LabelSet:
        try:
            return LabelSet.objects.get(name=labelset_name, version=labelset_version)
        except LabelSet.DoesNotExist as exc:
            raise CommandError(
                f"LabelSet name='{labelset_name}', version={labelset_version} does not exist. "
                "Create it first (e.g. via fixtures or admin)."
            ) from exc

    def _get_or_create_label_and_attach_to_labelset(
        self, label_name: str, labelset: LabelSet, dry_run: bool = False
    ) -> Label:
        if dry_run:
            label = Label.objects.filter(name=label_name).first()
            return label or Label(name=label_name)

        label, _ = Label.objects.get_or_create(name=label_name)
        # Attach to this labelset if missing
        if label not in labelset.labels.all():
            labelset.labels.add(label)
        return label

    class _noop_context:
        """Simple no-op context manager used for dry-run."""

        def __enter__(self) -> NullValue:
            return None

        def __exit__(
            self,
            exc_type: ExceptionTypeOrNull,
            exc_val: ExceptionValueOrNull,
            exc_tb: TracebackOrNull,
        ) -> bool:
            return False

    def _resolve_import_context(
        self, *, video_id: LegacyIntOrNull, center_id: LegacyIntOrNull
    ) -> tuple[VideoFileOrNull, Center]:
        if video_id is not None:
            try:
                video = VideoFile.objects.select_related("center").get(id=video_id)
            except VideoFile.DoesNotExist as exc:
                raise CommandError(
                    f"VideoFile with id={video_id} does not exist."
                ) from exc
            video_record = _legacy_video_record(video)
            if center_id is not None and video_record.center_id != center_id:
                raise CommandError(
                    f"--center-id={center_id} does not match VideoFile id={video_id} "
                    f"center_id={video_record.center_id}."
                )
            return video, video_record.center

        if center_id is None:
            raise CommandError(
                "Provide --center-id to backfill legacy old_examination_id values "
                "into VideoFile rows, or provide --video-id for fallback rows."
            )

        try:
            center = Center.objects.get(id=center_id)
        except Center.DoesNotExist as exc:
            raise CommandError(f"Center with id={center_id} does not exist.") from exc
        return None, center

    def _resolve_video_for_legacy_item(
        self,
        *,
        old_examination_id: LegacyTextOrNull,
        center: Center,
        fallback_video: VideoFileOrNull,
        dry_run: bool,
        cache: dict[str, VideoFile],
    ) -> VideoFile:
        if old_examination_id is None:
            if fallback_video is None:
                raise CommandError(
                    "Legacy row has no old_examination_id. Provide --video-id if "
                    "the import contains fallback rows without legacy examination IDs."
                )
            return fallback_video

        if old_examination_id in cache:
            return cache[old_examination_id]

        video_hash = self._build_legacy_video_hash(
            center_id=_persisted_record(center).id,
            old_examination_id=old_examination_id,
        )
        defaults = {
            "center": center,
            "original_file_name": f"{video_hash}.legacy",
        }
        if dry_run:
            video = VideoFile(video_hash=video_hash, **defaults)
            self.stdout.write(
                self.style.WARNING(
                    f"[DRY RUN] Would create/reuse VideoFile video_hash='{video_hash}'."
                )
            )
        else:
            video, created = VideoFile.objects.get_or_create(
                video_hash=video_hash,
                defaults=defaults,
            )
            video_record = _legacy_video_record(video)
            if created:
                self.stdout.write(
                    self.style.NOTICE(
                        f"Created synthetic legacy VideoFile id={video_record.id}, "
                        f"video_hash='{video_record.video_hash}'."
                    )
                )
        cache[old_examination_id] = video
        return video

    def _ensure_video_frame_dir(
        self,
        *,
        target_video: VideoFile,
        staged_images_root: Path,
        staged_dirs_by_video_key: dict[int | str, Path],
        dry_run: bool,
    ) -> Path:
        video_key = self._video_key(target_video)
        target_video_record = _legacy_video_record(target_video)
        if video_key in staged_dirs_by_video_key:
            return staged_dirs_by_video_key[video_key]

        video_staged_root = ensure_within_protected_root(
            staged_images_root / target_video_record.video_hash
        )
        staged_dirs_by_video_key[video_key] = video_staged_root

        if dry_run:
            self.stdout.write(
                self.style.WARNING(
                    "[DRY RUN] Would set frame_dir for VideoFile "
                    f"'{target_video_record.video_hash}' to '{video_staged_root}'."
                )
            )
            return video_staged_root

        ensure_directory(video_staged_root)
        if (
            not target_video_record.frame_dir
            or Path(target_video_record.frame_dir).resolve() != video_staged_root
        ):
            target_video_record.frame_dir = str(video_staged_root)
            target_video_record.save(update_fields=["frame_dir"])
            self.stdout.write(
                self.style.NOTICE(
                    f"Set frame_dir for VideoFile id={target_video_record.id} "
                    f"to '{target_video_record.frame_dir}'."
                )
            )
        return video_staged_root

    def _resolve_legacy_image_paths(
        self,
        *,
        images_root: Path,
        video_staged_root: Path,
        filename: str,
        line_num: int,
    ) -> tuple[Path, Path]:
        relative_filename = Path(filename)
        if relative_filename.is_absolute() or ".." in relative_filename.parts:
            raise CommandError(
                f"Unsafe filename on line {line_num}: '{filename}' must be relative and stay inside images_root."
            )

        source = (images_root / relative_filename).resolve()
        images_root_resolved = images_root.resolve()
        try:
            source.relative_to(images_root_resolved)
        except ValueError as exc:
            raise CommandError(
                f"Unsafe filename on line {line_num}: '{filename}' escapes images_root."
            ) from exc

        destination = ensure_within_protected_root(
            (video_staged_root / relative_filename).resolve()
        )
        try:
            destination.relative_to(video_staged_root.resolve())
        except ValueError as exc:
            raise CommandError(
                f"Unsafe filename on line {line_num}: '{filename}' escapes staged storage."
            ) from exc

        return source, destination

    def _next_frame_number(
        self, *, target_video: VideoFile, counters: dict[int | str, int]
    ) -> int:
        video_key = self._video_key(target_video)
        if video_key not in counters:
            if isinstance(video_key, str):
                counters[video_key] = 0
            else:
                counters[video_key] = (
                    Frame.objects.filter(video=target_video).aggregate(
                        max_frame_number=Max("frame_number")
                    )["max_frame_number"]
                    or 0
                )
        counters[video_key] += 1
        return counters[video_key]

    def _video_key(self, video: VideoFile) -> int | str:
        video_record = _legacy_video_record(video)
        return (
            video_record.pk if video_record.pk is not None else video_record.video_hash
        )

    def _build_legacy_video_hash(
        self, *, center_id: int, old_examination_id: str
    ) -> str:
        identity = f"center:{center_id}:old_examination_id:{old_examination_id}"
        digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:32]
        return f"legacy_exam_c{center_id}_{digest}"

    def _resolve_staged_images_root(
        self, *, raw_path: LegacyTextOrNull, import_key: str
    ) -> Path:
        if raw_path:
            return ensure_within_protected_root(Path(raw_path).expanduser().resolve())
        return ensure_within_protected_root(
            self._protected_migration_root() / "legacy_data" / import_key
        )

    def _resolve_manifest_path(
        self, *, raw_path: LegacyTextOrNull, import_key: str
    ) -> Path:
        if raw_path:
            return ensure_within_protected_root(Path(raw_path).expanduser().resolve())
        return ensure_within_protected_root(
            self._protected_migration_root()
            / "manifests"
            / "load_legacy_data"
            / f"{import_key}_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.json"
        )

    def _protected_migration_root(self) -> Path:
        return ensure_within_protected_root(
            EndoregPathsModel.from_environment().storage / "migration_staging"
        )
