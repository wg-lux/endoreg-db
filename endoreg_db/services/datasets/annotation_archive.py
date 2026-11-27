"""Bounded reader for the Full Export folder and ZIP layouts."""

import csv
import io
import stat
from collections import defaultdict
from collections.abc import Generator, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import IO, Annotated, Literal
from zipfile import ZIP_DEFLATED, ZIP_STORED, ZipFile, ZipInfo

from pydantic import BaseModel, ConfigDict, Field, field_validator

from endoreg_db.schemas.frame_annotation_export import (
    FRAME_ANNOTATION_EXPORT_MANIFEST_NAME,
    FrameAnnotationExportManifest,
    FrameAnnotationExportSegment,
    FrameAnnotationExportVideo,
)
from endoreg_db.utils.file_operations import atomic_write_file
from endoreg_db.utils.hashs import get_file_hash
from endoreg_db.utils.paths import normalize_protected_media_relative_path

_MANIFEST_LIMIT = 32 * 1024**2
_TABLE_LIMIT = 512 * 1024**2
_CHUNK_SIZE = 1024**2
_ZIP_MEMBER_LIMIT = 2_000_000
_SPREADSHEET_PREFIXES = ("=", "+", "-", "@", "\t", "\r", "\n")

type ContentHash = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class ExportAnnotation(BaseModel):
    """Portable fields of one standard annotation CSV row."""

    model_config = ConfigDict(extra="ignore", frozen=True, allow_inf_nan=False)

    annotation_id: int = Field(gt=0)
    video_id: int = Field(gt=0)
    raw_video_hash: ContentHash
    artifact_kind: Literal["processed"]
    artifact_sha256: ContentHash
    frame_number: int = Field(ge=0)
    frame_relative_path: str
    presentation_timestamp: int = Field(ge=0)
    presentation_time_seconds: float = Field(ge=0)
    stream_time_base_num: int = Field(gt=0)
    stream_time_base_den: int = Field(gt=0)
    label_id: int = Field(gt=0)
    label_name: str = Field(min_length=1, max_length=255)
    information_source_id: int = Field(gt=0)
    information_source_name: str = Field(min_length=1, max_length=100)
    annotator: str
    value: bool
    float_value: float | None

    @field_validator(
        "label_name", "information_source_name", "annotator", mode="before"
    )
    @classmethod
    def decode_text(cls, value: object) -> object:
        if (
            isinstance(value, str)
            and value.startswith("'")
            and value[1:].startswith(_SPREADSHEET_PREFIXES)
        ):
            return value[1:]
        return value

    @field_validator("float_value", mode="before")
    @classmethod
    def empty_score(cls, value: object) -> object:
        return None if value == "" else value


@dataclass(frozen=True, slots=True)
class ExportVideo:
    manifest: FrameAnnotationExportVideo
    raw_hash: str
    annotations: list[ExportAnnotation]
    segments: list[FrameAnnotationExportSegment]


@dataclass(frozen=True, slots=True)
class ExportFiles:
    path: Path
    archive: ZipFile | None
    members: dict[str, ZipInfo]
    prefix: str = ""

    def _path(self, name: str) -> Path:
        result = self.path / name
        if result.resolve() != result:
            raise ValueError("The export contains a symbolic link")
        return result

    def exists(self, name: str) -> bool:
        if self.archive is not None:
            return self.prefix + name in self.members
        return self._path(name).is_file()

    def has_frames(self) -> bool:
        if self.archive is not None:
            return any(
                name.startswith(self.prefix + "frames/") for name in self.members
            )
        return self._path("frames").is_dir()

    def stage(self, name: str, destination: Path, *, limit: int | None = None) -> None:
        name = _member_path(name)
        if self.archive is None:
            source = self._path(name)
            if not source.is_file():
                raise ValueError(f"Missing export member {name}")
            size = source.stat().st_size
            _check_size(size, limit)
            with source.open("rb") as handle:
                _stage_stream(handle, size, destination)
        else:
            member = self.members.get(self.prefix + name)
            if member is None:
                raise ValueError(f"Missing export member {name}")
            _check_size(member.file_size, limit)
            with self.archive.open(member) as handle:
                _stage_stream(handle, member.file_size, destination)


def _member_path(name: str) -> str:
    normalized = normalize_protected_media_relative_path(name)
    if normalized != name or "\\" in name or ":" in name or "\0" in name:
        raise ValueError("Invalid export member path")
    return normalized


def _file_name(name: str, suffix: str | None = None) -> str:
    path = PurePosixPath(_member_path(name))
    if len(path.parts) != 1 or (suffix and path.suffix.lower() != suffix):
        raise ValueError("Invalid file name in the export")
    return name


def _check_size(size: int, limit: int | None) -> None:
    if size <= 0 or (limit is not None and size > limit):
        raise ValueError("Invalid export member size")


def _stage_stream(handle: IO[bytes], size: int, destination: Path) -> None:
    def chunks() -> Iterator[bytes]:
        remaining = size
        while chunk := handle.read(_CHUNK_SIZE):
            remaining -= len(chunk)
            if remaining < 0:
                raise ValueError("Export member exceeds its declared size")
            yield chunk
        if remaining:
            raise ValueError("Export member ended before its declared size")

    atomic_write_file(
        destination=destination, content=chunks(), required_bytes=size, file_mode=0o600
    )


@contextmanager
def open_export(path: Path) -> Generator[ExportFiles]:
    if path.is_dir():
        yield ExportFiles(path, None, {})
        return
    if not path.is_file() or path.suffix.lower() != ".zip":
        raise ValueError("Select a Full Export folder or ZIP")
    with ZipFile(path) as archive:
        entries = archive.infolist()
        if len(entries) > _ZIP_MEMBER_LIMIT:
            raise ValueError("The export contains too many ZIP members")
        members: dict[str, ZipInfo] = {}
        names: set[str] = set()
        for entry in entries:
            name = _member_path(entry.orig_filename.removesuffix("/"))
            mode = stat.S_IFMT(entry.external_attr >> 16)
            if name in names or mode not in {0, stat.S_IFDIR, stat.S_IFREG}:
                raise ValueError(
                    "The export contains a duplicate or special ZIP member"
                )
            if entry.flag_bits & 1 or entry.compress_type not in {
                ZIP_STORED,
                ZIP_DEFLATED,
            }:
                raise ValueError(
                    "The export ZIP uses unsupported encryption or compression"
                )
            names.add(name)
            if not entry.is_dir():
                members[name] = entry
        manifests = [
            name
            for name in members
            if PurePosixPath(name).name == FRAME_ANNOTATION_EXPORT_MANIFEST_NAME
        ]
        if len(manifests) != 1 or len(PurePosixPath(manifests[0]).parts) > 2:
            raise ValueError("Expected one Full Export manifest in the ZIP")
        prefix = manifests[0].removesuffix(FRAME_ANNOTATION_EXPORT_MANIFEST_NAME)
        yield ExportFiles(path, archive, members, prefix)


def read_export(
    files: ExportFiles, scratch: Path
) -> tuple[list[ExportVideo], dict[int, str], str, str]:
    manifest_path = scratch / FRAME_ANNOTATION_EXPORT_MANIFEST_NAME
    files.stage(
        FRAME_ANNOTATION_EXPORT_MANIFEST_NAME, manifest_path, limit=_MANIFEST_LIMIT
    )
    manifest = FrameAnnotationExportManifest.model_validate_json(
        manifest_path.read_bytes()
    )
    table_path = scratch / "annotations.csv"
    files.stage(
        _file_name(manifest.annotation_file_name, ".csv"),
        table_path,
        limit=_TABLE_LIMIT,
    )

    videos = {item.video_id: item for item in manifest.videos}
    if len(videos) != len(manifest.videos) or len(videos) != len(
        {item.video_file_name for item in manifest.videos}
    ):
        raise ValueError("The manifest repeats a video identity or file name")
    for video in videos.values():
        name = _file_name(video.video_file_name, ".mp4")
        if not video.video_file_exported or not files.exists(f"videos/{name}"):
            raise ValueError(
                f"Full Export is missing a processed video for {video.video_id}"
            )

    grouped: dict[int, list[ExportAnnotation]] = defaultdict(list)
    source_names: dict[int, str] = {}
    label_names: dict[int, str] = {}
    raw_hashes: dict[int, str] = {}
    annotation_ids: set[int] = set()
    frames_included = files.has_frames()
    checked_frames: set[str] = set()
    with (
        table_path.open("rb") as table,
        io.TextIOWrapper(table, encoding="utf-8", newline="") as text,
    ):
        reader = csv.DictReader(text, strict=True)
        headings = reader.fieldnames or []
        if len(set(headings)) != len(
            headings
        ) or not ExportAnnotation.model_fields.keys() <= set(headings):
            raise ValueError(
                "Expected the standard presentation-timestamp annotation CSV"
            )
        for values in reader:
            if None in values or None in values.values():
                raise ValueError(f"Malformed annotation CSV row {reader.line_num}")
            try:
                row = ExportAnnotation.model_validate(values)
            except ValueError as error:
                raise ValueError(
                    f"Invalid annotation CSV row {reader.line_num}"
                ) from error
            video = videos.get(row.video_id)
            if (
                video is None
                or row.annotation_id in annotation_ids
                or row.artifact_sha256 != video.artifact_sha256
                or (row.stream_time_base_num, row.stream_time_base_den)
                != (video.stream_time_base_num, video.stream_time_base_den)
                or raw_hashes.setdefault(row.video_id, row.raw_video_hash)
                != row.raw_video_hash
                or source_names.setdefault(
                    row.information_source_id, row.information_source_name
                )
                != row.information_source_name
                or label_names.setdefault(row.label_id, row.label_name)
                != row.label_name
            ):
                raise ValueError(
                    f"Inconsistent identities in annotation CSV row {reader.line_num}"
                )
            annotation_ids.add(row.annotation_id)
            if row.frame_relative_path:
                name = _member_path(row.frame_relative_path)
                frame = f"frames/video_{row.video_id}/{name}"
                if frames_included and frame not in checked_frames:
                    if not files.exists(frame):
                        raise ValueError(
                            f"The export omits an image referenced by row {reader.line_num}"
                        )
                    checked_frames.add(frame)
            elif frames_included:
                raise ValueError(
                    f"Missing image name in annotation CSV row {reader.line_num}"
                )
            grouped[row.video_id].append(row)

    segments: dict[int, list[FrameAnnotationExportSegment]] = defaultdict(list)
    segment_ids: set[int] = set()
    for segment in manifest.segments:
        video = videos.get(segment.video_id)
        if (
            video is None
            or segment.segment_id in segment_ids
            or not 1 <= len(segment.label_name) <= 255
            or label_names.setdefault(segment.label_id, segment.label_name)
            != segment.label_name
            or (segment.stream_time_base_num, segment.stream_time_base_den)
            != (video.stream_time_base_num, video.stream_time_base_den)
        ):
            raise ValueError(
                "Inconsistent segment identity or time base in the manifest"
            )
        segment_ids.add(segment.segment_id)
        segments[segment.video_id].append(segment)
    if videos.keys() != grouped.keys():
        raise ValueError("Every Full Export video must have annotation rows")
    return (
        [
            ExportVideo(video, raw_hashes[key], grouped[key], segments[key])
            for key, video in videos.items()
        ],
        source_names,
        get_file_hash(manifest_path),
        get_file_hash(table_path),
    )
