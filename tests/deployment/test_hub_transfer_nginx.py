from __future__ import annotations

import ipaddress
import os
import queue
import shutil
import socket
import subprocess
import time
from contextlib import suppress
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

import pytest
import requests
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID
from requests.exceptions import ConnectionError as RequestsConnectionError
from requests.exceptions import SSLError

NGINX = shutil.which("nginx")
CONFIG_PATH = (
    Path(__file__).resolve().parents[2] / "deployment" / "nginx" / "hub-transfer.conf"
)


def _unused_tcp_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _certificate_authority(
    common_name: str,
) -> tuple[rsa.RSAPrivateKey, x509.Certificate]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    now = datetime.now(timezone.utc)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])
    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=2))
        .add_extension(x509.BasicConstraints(ca=True, path_length=1), critical=True)
        .sign(key, hashes.SHA256())
    )
    return key, certificate


def _leaf_certificate(
    *,
    common_name: str,
    issuer_key: rsa.RSAPrivateKey,
    issuer_certificate: x509.Certificate,
    client: bool,
) -> tuple[rsa.RSAPrivateKey, x509.Certificate]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    now = datetime.now(timezone.utc)
    builder = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)]))
        .issuer_name(issuer_certificate.subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(
            x509.ExtendedKeyUsage(
                [
                    ExtendedKeyUsageOID.CLIENT_AUTH
                    if client
                    else ExtendedKeyUsageOID.SERVER_AUTH
                ]
            ),
            critical=False,
        )
    )
    if not client:
        builder = builder.add_extension(
            x509.SubjectAlternativeName(
                [x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]
            ),
            critical=False,
        )
    return key, builder.sign(issuer_key, hashes.SHA256())


def _write_identity(
    directory: Path,
    name: str,
    key: rsa.RSAPrivateKey,
    certificate: x509.Certificate,
) -> tuple[Path, Path]:
    certificate_path = directory / f"{name}.crt"
    key_path = directory / f"{name}.key"
    certificate_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    os.chmod(key_path, 0o600)
    return certificate_path, key_path


def _write_certificates(path: Path, *certificates: x509.Certificate) -> None:
    path.write_bytes(
        b"".join(
            certificate.public_bytes(serialization.Encoding.PEM)
            for certificate in certificates
        )
    )


class _CapturingBackend(BaseHTTPRequestHandler):
    received_headers: queue.Queue[dict[str, str]] = queue.Queue()

    def do_POST(self) -> None:  # noqa: N802
        content_length = int(self.headers.get("Content-Length", "0"))
        self.rfile.read(content_length)
        self.received_headers.put(dict(self.headers.items()))
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"ok": true}')

    def log_message(self, format: str, *args: object) -> None:
        del format, args


def _post(
    *,
    port: int,
    server_ca: Path,
    client_identity: tuple[Path, Path] | None,
) -> requests.Response:
    return requests.post(
        f"https://127.0.0.1:{port}/api/media/hub/transfers/",
        data=b'{"probe": true}',
        headers={
            "Content-Type": "application/json",
            "X-Forwarded-Proto": "http",
            "X-Client-Cert-Verified": "ATTACKER-SUPPLIED",
        },
        cert=(
            (str(client_identity[0]), str(client_identity[1]))
            if client_identity is not None
            else None
        ),
        verify=str(server_ca),
        timeout=3,
    )


def _assert_rejected_before_backend(
    *,
    port: int,
    server_ca: Path,
    client_identity: tuple[Path, Path] | None,
) -> None:
    try:
        response = _post(
            port=port,
            server_ca=server_ca,
            client_identity=client_identity,
        )
    except SSLError:
        pass
    else:
        assert response.status_code >= 400
    assert _CapturingBackend.received_headers.empty()


def _wait_until_rejected_before_backend(
    *,
    port: int,
    server_ca: Path,
    client_identity: tuple[Path, Path],
    timeout_seconds: float = 5,
) -> None:
    deadline = time.monotonic() + timeout_seconds
    while True:
        try:
            response = _post(
                port=port,
                server_ca=server_ca,
                client_identity=client_identity,
            )
        except SSLError:
            assert _CapturingBackend.received_headers.empty()
            return
        if response.status_code >= 400:
            assert _CapturingBackend.received_headers.empty()
            return

        assert response.status_code == 200
        _CapturingBackend.received_headers.get(timeout=1)
        if time.monotonic() >= deadline:
            pytest.fail("nginx did not retire the previous client CA")
        time.sleep(0.05)


def _reload_nginx(*, prefix: Path, config: Path) -> None:
    reload_result = subprocess.run(
        [str(NGINX), "-p", str(prefix), "-c", str(config), "-s", "reload"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert reload_result.returncode == 0, reload_result.stderr


def _write_nginx_config(*, path: Path, prefix: Path, rendered_include: str) -> None:
    path.write_text(
        f"pid {prefix / 'nginx.pid'};\n"
        "error_log stderr notice;\n"
        "events {}\n"
        "http {\n"
        "access_log off;\n"
        f"{rendered_include}\n"
        "}\n",
        encoding="utf-8",
    )


@pytest.mark.skipif(NGINX is None, reason="nginx executable is not available")
def test_nginx_mtls_strips_spoofed_headers_and_rotates_client_ca(
    tmp_path: Path,
) -> None:
    server_ca_key, server_ca_certificate = _certificate_authority("server-ca")
    client_ca_1_key, client_ca_1_certificate = _certificate_authority("client-ca-1")
    client_ca_2_key, client_ca_2_certificate = _certificate_authority("client-ca-2")
    wrong_ca_key, wrong_ca_certificate = _certificate_authority("wrong-client-ca")
    server_key, server_certificate = _leaf_certificate(
        common_name="hub-transfer",
        issuer_key=server_ca_key,
        issuer_certificate=server_ca_certificate,
        client=False,
    )
    client_1_key, client_1_certificate = _leaf_certificate(
        common_name="site-node-1",
        issuer_key=client_ca_1_key,
        issuer_certificate=client_ca_1_certificate,
        client=True,
    )
    client_2_key, client_2_certificate = _leaf_certificate(
        common_name="site-node-2",
        issuer_key=client_ca_2_key,
        issuer_certificate=client_ca_2_certificate,
        client=True,
    )
    wrong_client_key, wrong_client_certificate = _leaf_certificate(
        common_name="untrusted-site-node",
        issuer_key=wrong_ca_key,
        issuer_certificate=wrong_ca_certificate,
        client=True,
    )

    server_ca_path = tmp_path / "server-ca.crt"
    client_ca_path = tmp_path / "client-ca.crt"
    _write_certificates(server_ca_path, server_ca_certificate)
    _write_certificates(client_ca_path, client_ca_1_certificate)
    server_identity = _write_identity(
        tmp_path, "server", server_key, server_certificate
    )
    client_1_identity = _write_identity(
        tmp_path, "client-1", client_1_key, client_1_certificate
    )
    client_2_identity = _write_identity(
        tmp_path, "client-2", client_2_key, client_2_certificate
    )
    wrong_client_identity = _write_identity(
        tmp_path, "wrong-client", wrong_client_key, wrong_client_certificate
    )

    backend_port = _unused_tcp_port()
    proxy_port = _unused_tcp_port()
    backend = ThreadingHTTPServer(("127.0.0.1", backend_port), _CapturingBackend)
    backend_thread = Thread(target=backend.serve_forever, daemon=True)
    backend_thread.start()

    rendered_include = (
        CONFIG_PATH.read_text(encoding="utf-8")
        .replace("listen 443 ssl;", f"listen 127.0.0.1:{proxy_port} ssl;")
        .replace(
            "/run/credentials/endoreg-hub-transfer/server.crt",
            str(server_identity[0]),
        )
        .replace(
            "/run/credentials/endoreg-hub-transfer/server.key",
            str(server_identity[1]),
        )
        .replace(
            "/run/credentials/endoreg-hub-transfer/client-ca.crt",
            str(client_ca_path),
        )
        .replace("127.0.0.1:8000", f"127.0.0.1:{backend_port}")
    )
    nginx_config = tmp_path / "nginx.conf"
    _write_nginx_config(
        path=nginx_config,
        prefix=tmp_path,
        rendered_include=rendered_include,
    )
    nginx_process = subprocess.Popen(
        [str(NGINX), "-p", str(tmp_path), "-c", str(nginx_config), "-g", "daemon off;"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    try:
        deadline = time.monotonic() + 5
        while True:
            try:
                valid_response = _post(
                    port=proxy_port,
                    server_ca=server_ca_path,
                    client_identity=client_1_identity,
                )
                break
            except RequestsConnectionError:
                if nginx_process.poll() is not None:
                    _, nginx_stderr = nginx_process.communicate(timeout=1)
                    pytest.fail(f"nginx exited during startup: {nginx_stderr}")
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.05)
        assert valid_response.status_code == 200
        forwarded = _CapturingBackend.received_headers.get(timeout=1)
        assert forwarded["X-Forwarded-Proto"] == "https"
        assert forwarded["X-Client-Cert-Verified"] == "SUCCESS"

        for rejected_identity in (None, wrong_client_identity, client_2_identity):
            _assert_rejected_before_backend(
                port=proxy_port,
                server_ca=server_ca_path,
                client_identity=rejected_identity,
            )

        overlap_ca_path = tmp_path / "client-ca-overlap.crt"
        _write_certificates(
            overlap_ca_path,
            client_ca_1_certificate,
            client_ca_2_certificate,
        )
        _write_nginx_config(
            path=nginx_config,
            prefix=tmp_path,
            rendered_include=rendered_include.replace(
                str(client_ca_path), str(overlap_ca_path)
            ),
        )
        _reload_nginx(prefix=tmp_path, config=nginx_config)
        deadline = time.monotonic() + 5
        while True:
            try:
                rotated_response = _post(
                    port=proxy_port,
                    server_ca=server_ca_path,
                    client_identity=client_2_identity,
                )
            except SSLError:
                rotated_response = None
            if rotated_response is not None and rotated_response.status_code == 200:
                break
            if time.monotonic() >= deadline:
                pytest.fail(
                    "nginx did not activate the staged client CA bundle: "
                    f"status={getattr(rotated_response, 'status_code', None)} "
                    f"body={getattr(rotated_response, 'text', '')!r}"
                )
            time.sleep(0.05)
        assert rotated_response.status_code == 200
        rotated_headers = _CapturingBackend.received_headers.get(timeout=1)
        assert rotated_headers["X-Client-Cert-Verified"] == "SUCCESS"
        old_identity_during_overlap = _post(
            port=proxy_port,
            server_ca=server_ca_path,
            client_identity=client_1_identity,
        )
        assert old_identity_during_overlap.status_code == 200
        _CapturingBackend.received_headers.get(timeout=1)

        new_ca_path = tmp_path / "client-ca-2.crt"
        _write_certificates(new_ca_path, client_ca_2_certificate)
        _write_nginx_config(
            path=nginx_config,
            prefix=tmp_path,
            rendered_include=rendered_include.replace(
                str(client_ca_path), str(new_ca_path)
            ),
        )
        _reload_nginx(prefix=tmp_path, config=nginx_config)
        deadline = time.monotonic() + 5
        while True:
            new_identity_response = _post(
                port=proxy_port,
                server_ca=server_ca_path,
                client_identity=client_2_identity,
            )
            if new_identity_response.status_code == 200:
                _CapturingBackend.received_headers.get(timeout=1)
                break
            if time.monotonic() >= deadline:
                pytest.fail("nginx did not retire the previous client CA")
            time.sleep(0.05)
        _wait_until_rejected_before_backend(
            port=proxy_port,
            server_ca=server_ca_path,
            client_identity=client_1_identity,
        )
    finally:
        nginx_process.terminate()
        with suppress(subprocess.TimeoutExpired):
            nginx_process.wait(timeout=5)
        if nginx_process.poll() is None:
            nginx_process.kill()
            nginx_process.wait(timeout=5)
        backend.shutdown()
        backend.server_close()
        backend_thread.join(timeout=5)
