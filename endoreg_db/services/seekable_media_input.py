from __future__ import annotations

import logging
import secrets
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Generator, Protocol, cast
from urllib.parse import urlsplit

from endoreg_db.utils.storage_streaming import (
    field_file_size,
    iter_field_file_bytes,
    parse_byte_range,
)

logger = logging.getLogger(__name__)


class SeekableFieldFile(Protocol):
    name: str


@dataclass(frozen=True)
class SeekableMediaInput:
    url: str
    plaintext_size: int


class _SeekableRangeServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(
        self,
        field_file: SeekableFieldFile,
        *,
        token: str,
        plaintext_size: int,
    ) -> None:
        self.field_file = field_file
        self.token_path = f"/{token}/video.mp4"
        self.plaintext_size = plaintext_size
        super().__init__(("127.0.0.1", 0), _SeekableRangeRequestHandler)


class _SeekableRangeRequestHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args: object) -> None:
        return

    def _range_server(self) -> _SeekableRangeServer:
        return cast(_SeekableRangeServer, self.server)

    def _request_is_authorized(self) -> bool:
        server = self._range_server()
        return (
            self.client_address[0] == "127.0.0.1"
            and urlsplit(self.path).path == server.token_path
        )

    def _send_headers(self) -> tuple[int, int] | None:
        server = self._range_server()
        if not self._request_is_authorized():
            self.send_error(404)
            return None

        range_header = self.headers.get("Range")
        if range_header is None:
            start = 0
            end = server.plaintext_size - 1
            status = 200
        else:
            try:
                byte_range = parse_byte_range(range_header, server.plaintext_size)
            except ValueError:
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{server.plaintext_size}")
                self.send_header("Connection", "close")
                self.end_headers()
                return None
            start = byte_range.start
            end = byte_range.end
            status = 206

        self.send_response(status)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Type", "video/mp4")
        self.send_header("Content-Length", str(end - start + 1))
        if status == 206:
            self.send_header(
                "Content-Range",
                f"bytes {start}-{end}/{server.plaintext_size}",
            )
        self.send_header("Connection", "close")
        self.end_headers()
        return start, end

    def do_HEAD(self) -> None:
        self._send_headers()

    def do_GET(self) -> None:
        selected_range = self._send_headers()
        if selected_range is None:
            return
        start, end = selected_range
        server = self._range_server()
        try:
            for chunk in iter_field_file_bytes(
                server.field_file,
                start=start,
                end=end,
            ):
                self.wfile.write(chunk)
        except (BrokenPipeError, ConnectionResetError):
            logger.debug("FFmpeg closed the seekable media range connection early")


@contextmanager
def serve_seekable_media_input(
    field_file: SeekableFieldFile,
) -> Generator[SeekableMediaInput, None, None]:
    """Expose one FieldFile to local FFmpeg as a seekable HTTP byte source."""
    plaintext_size = field_file_size(field_file)
    if plaintext_size <= 0:
        raise ValueError("Seekable media input must not be empty")

    token = secrets.token_urlsafe(32)
    server = _SeekableRangeServer(
        field_file,
        token=token,
        plaintext_size=plaintext_size,
    )
    thread = threading.Thread(
        target=server.serve_forever,
        name="seekable-media-range",
        daemon=True,
    )
    thread.start()
    host, port = cast(tuple[str, int], server.server_address)
    try:
        yield SeekableMediaInput(
            url=f"http://{host}:{port}{server.token_path}",
            plaintext_size=plaintext_size,
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
