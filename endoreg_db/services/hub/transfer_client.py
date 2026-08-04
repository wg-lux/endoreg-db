from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import time
from typing import Any

import requests
from endoreg_db.services.hub.transfer_logging import (
    info,
    json_block,
    kv,
    subsection,
)


@dataclass(frozen=True)
class HubTransferClient:
    base_url: str
    node_key: str
    node_secret: str
    timeout: int = 900
    # requests accepts either:
    # - True: use the system CA store
    # - False: disable server-certificate verification
    # - a path: use the specified CA bundle
    verify_tls: bool = True
    # Mutual-TLS client identity. Both values must be supplied together.
    client_certificate_file: Path | None = None
    client_key_file: Path | None = None

    def _url(self, path: str) -> str:
        return f"{self.base_url.rstrip('/')}/{path.lstrip('/')}"

    def _headers(self) -> dict[str, str]:
        return {
            "X-Network-Node-Key": self.node_key,
            "X-Network-Node-Secret": self.node_secret,
        }

    def _verify_argument(self) -> bool | str:
        if isinstance(self.verify_tls, str):
            ca_path = Path(self.verify_tls).expanduser().resolve()
            if not ca_path.is_file():
                raise FileNotFoundError(f"TLS CA bundle not found: {ca_path}")
            return str(ca_path)

        return self.verify_tls

    def _client_certificate_argument(
        self,
    ) -> tuple[str, str] | None:
        cert_file = self.client_certificate_file
        key_file = self.client_key_file

        if cert_file is None and key_file is None:
            return None

        if cert_file is None or key_file is None:
            raise ValueError(
                "Both client_certificate_file and client_key_file "
                "must be configured for mutual TLS"
            )

        cert_path = cert_file.expanduser().resolve()
        key_path = key_file.expanduser().resolve()

        if not cert_path.is_file():
            raise FileNotFoundError(f"mTLS client certificate not found: {cert_path}")

        if not key_path.is_file():
            raise FileNotFoundError(f"mTLS client private key not found: {key_path}")

        return str(cert_path), str(key_path)

    def _request_kwargs(self) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "headers": self._headers(),
            "timeout": self.timeout,
            "verify": self._verify_argument(),
        }

        client_certificate = self._client_certificate_argument()
        if client_certificate is not None:
            kwargs["cert"] = client_certificate

        return kwargs

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
            url,
            json=payload,
            **self._request_kwargs(),
        )

        elapsed = time.monotonic() - started

        kv("HTTP status", res.status_code)
        kv("Elapsed seconds", f"{elapsed:.3f}")
        kv("Response content type", res.headers.get("Content-Type"))
        kv("Response bytes", len(res.content))

        return self._response_json(
            res,
            operation="Hub transfer creation",
        )

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
                files={
                    "file": (
                        file_path.name,
                        fh,
                        content_type,
                    )
                },
                data={"media_role": "processed"},
                **self._request_kwargs(),
            )

        return self._response_json(res, operation="Hub transfer media upload")

    def get_status(self, transfer_key: str) -> dict[str, Any]:
        res = requests.get(
            self._url(f"/api/media/hub/transfers/{transfer_key}/status/"),
            **self._request_kwargs(),
        )
        return self._response_json(res, operation="Hub transfer status request")
