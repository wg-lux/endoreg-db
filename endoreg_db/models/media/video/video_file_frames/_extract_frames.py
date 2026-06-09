"""Compatibility alias for the service-layer video implementation."""

from __future__ import annotations

from endoreg_db.models.media.video._service_alias import alias_service_module

alias_service_module(
    __name__, "endoreg_db.services.video_files._frames._extract_frames"
)
