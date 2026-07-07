from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests


@dataclass(frozen=True)
class HubTransferClient:
    base_url: str
    node_key: str
    node_secret: str
    timeout: int = 900
    verify_tls: bool = True

    def _url(self, path: str) -> str:
        return f"{self.base_url.rstrip('/')}/{path.lstrip('/')}"

    def _headers(self) -> dict[str, str]:
        return {
            "X-Network-Node-Key": self.node_key,
            "X-Network-Node-Secret": self.node_secret,
        }

    def create_transfer(self, payload: dict[str, Any]) -> dict[str, Any]:
        res = requests.post(
            self._url("/api/media/hub/transfers/"),
            json=payload,
            headers=self._headers(),
            timeout=self.timeout,
            verify=self.verify_tls,
        )
        res.raise_for_status()
        return res.json()

    def upload_processed_media(
        self,
        *,
        transfer_key: str,
        file_path: Path,
        content_type: str,
    ) -> dict[str, Any]:
        file_path = file_path.expanduser().resolve()
        if not file_path.is_file():
            raise FileNotFoundError(f"Processed media not found: {file_path}")

        with file_path.open("rb") as fh:
            res = requests.post(
                self._url(f"/api/media/hub/transfers/{transfer_key}/media/"),
                headers=self._headers(),
                files={"file": (file_path.name, fh, content_type)},
                data={"media_role": "processed"},
                timeout=self.timeout,
                verify=self.verify_tls,
            )

        res.raise_for_status()
        return res.json()

    def get_status(self, transfer_key: str) -> dict[str, Any]:
        res = requests.get(
            self._url(f"/api/media/hub/transfers/{transfer_key}/status/"),
            headers=self._headers(),
            timeout=self.timeout,
            verify=self.verify_tls,
        )
        res.raise_for_status()
        return res.json()