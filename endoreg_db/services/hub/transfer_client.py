from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests
import time
from endoreg_db.services.hub.transfer_logging import (
    error,
    info,
    json_block,
    kv,
    path_info,
    step,
    subsection,
    success,
)
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
    
    @staticmethod
    def _response_json(res: requests.Response, *, operation: str) -> dict[str, Any]:
        if not res.ok:
            raise RuntimeError(
                f"{operation} failed: "
                f"status={res.status_code}, "
                f"url={res.url}, "
                f"response={res.text}"
            )
    
        try:
            data = res.json()
        except requests.exceptions.JSONDecodeError as exc:
            raise RuntimeError(
                f"{operation} returned invalid JSON: "
                f"status={res.status_code}, "
                f"url={res.url}, "
                f"response={res.text}"
            ) from exc
    
        if not isinstance(data, dict):
            raise RuntimeError(
                f"{operation} returned an unexpected response type: "
                f"{type(data).__name__}"
            )
    
        return data

    def create_transfer(self, payload: dict[str, Any]) -> dict[str, Any]:

        url = self._url("/api/media/hub/transfers/")
    
        subsection("HTTP create-transfer request")
        kv("Method", "POST")
        kv("URL", url)
        kv("TLS verification", self.verify_tls)
        kv("Source node header", self.node_key)
        info("X-Network-Node-Secret is present but intentionally not printed")
        json_block("Request JSON body", payload)
        started = time.monotonic()

        res = requests.post(
            self._url("/api/media/hub/transfers/"),
            json=payload,
            headers=self._headers(),
            timeout=self.timeout,
            verify=self.verify_tls,
        )
        
        elapsed = time.monotonic() - started

        kv("HTTP status", res.status_code)
        kv("Elapsed seconds", f"{elapsed:.3f}")
        kv("Response content type", res.headers.get("Content-Type"))
        kv("Response bytes", len(res.content))
    
        return self._response_json(
            res,
            operation="Hub transfer creation",)

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

        return self._response_json(res, operation="Hub transfer media upload")

    def get_status(self, transfer_key: str) -> dict[str, Any]:
        res = requests.get(
            self._url(f"/api/media/hub/transfers/{transfer_key}/status/"),
            headers=self._headers(),
            timeout=self.timeout,
            verify=self.verify_tls,
        )
        return self._response_json(res, operation="Hub transfer status request")