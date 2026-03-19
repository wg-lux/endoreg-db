from pathlib import Path


def sha256_file_hex(path: Path, chunk_size: int = ...) -> str: ...


def render_single_page_pdf(text: str) -> bytes: ...


def parse_extracted_frame_numbers(paths: list[str]) -> list[int]: ...
