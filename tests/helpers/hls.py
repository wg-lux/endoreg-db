from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import BinaryIO


@dataclass(frozen=True)
class HlsKeyInfoRecord:
    key_uri: str
    key_path: Path
    iv_hex: str


def _empty_bytes_list() -> list[bytes]:
    return []


def _empty_key_info_record_list() -> list[HlsKeyInfoRecord]:
    return []


@dataclass
class FakeHlsOutputRecorder:
    segment_payload: bytes = b"encrypted segment"
    include_version_tag: bool = True
    source_payloads: list[bytes] = field(default_factory=_empty_bytes_list, init=False)
    key_info_records: list[HlsKeyInfoRecord] = field(
        default_factory=_empty_key_info_record_list,
        init=False,
    )
    content_key_payloads: list[bytes] = field(
        default_factory=_empty_bytes_list, init=False
    )

    def run(
        self,
        *,
        source: BinaryIO,
        source_file_name: str,
        source_size_bytes: int | None,
        temp_source_dir: Path,
        key_info_path: Path,
        segment_pattern: Path,
        playlist_path: Path,
        segment_base_url: str,
    ) -> None:
        _ = source_file_name
        _ = source_size_bytes
        _ = temp_source_dir
        record = _read_key_info_record(key_info_path)
        if not record.key_path.exists():
            raise AssertionError(f"HLS key file does not exist: {record.key_path}")

        self.key_info_records.append(record)
        self.content_key_payloads.append(record.key_path.read_bytes())
        self.source_payloads.append(source.read())
        write_fake_hls_playlist_and_segment(
            key_uri=record.key_uri,
            iv_hex=record.iv_hex,
            segment_pattern=segment_pattern,
            playlist_path=playlist_path,
            segment_base_url=segment_base_url,
            segment_payload=self.segment_payload,
            include_version_tag=self.include_version_tag,
        )


def write_fake_hls_playlist_and_segment(
    *,
    key_uri: str,
    iv_hex: str,
    segment_pattern: Path,
    playlist_path: Path,
    segment_base_url: str,
    segment_payload: bytes = b"encrypted segment",
    include_version_tag: bool = True,
) -> None:
    lines = ["#EXTM3U"]
    if include_version_tag:
        lines.append("#EXT-X-VERSION:3")
    lines.extend(
        [
            f'#EXT-X-KEY:METHOD=AES-128,URI="{key_uri}",IV=0x{iv_hex}',
            f"{segment_base_url}seg_000.ts",
            "#EXT-X-ENDLIST",
        ]
    )
    playlist_path.write_text("\n".join(lines), encoding="utf-8")
    segment_pattern.parent.mkdir(parents=True, exist_ok=True)
    (segment_pattern.parent / "seg_000.ts").write_bytes(segment_payload)


def _read_key_info_record(key_info_path: Path) -> HlsKeyInfoRecord:
    key_info_lines = key_info_path.read_text(encoding="utf-8").splitlines()
    if len(key_info_lines) != 3:
        raise AssertionError(
            f"Expected three HLS key info lines, got {len(key_info_lines)}"
        )
    return HlsKeyInfoRecord(
        key_uri=key_info_lines[0],
        key_path=Path(key_info_lines[1]),
        iv_hex=key_info_lines[2],
    )
