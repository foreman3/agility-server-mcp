"""Exercise certificate verification over real HTTPS, using an ephemeral self-signed CA."""

import asyncio
import ipaddress
import json
import os
import ssl
from datetime import UTC, datetime, timedelta

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from fastmcp import Client
from fastmcp.client.transports import StdioTransport

from agility_mcp import cli
from agility_mcp.client import AgilityClient, create_http_client
from agility_mcp.config import load_config
from agility_mcp.errors import AgilityError
from agility_mcp.server import create_server


class Store:
    async def status(self, target):
        return {"found": True}

    async def get(self, target):
        return {"username": "synthetic", "secret": "synthetic-tls-token"}


@pytest.fixture
async def self_signed_agility(tmp_path, monkeypatch):
    monkeypatch.setenv("NO_PROXY", "localhost,127.0.0.1")
    monkeypatch.delenv("SSL_CERT_FILE", raising=False)
    monkeypatch.delenv("SSL_CERT_DIR", raising=False)
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    now = datetime.now(UTC)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(
            x509.SubjectAlternativeName(
                [x509.DNSName("localhost"), x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]
            ),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )
    cert_path, key_path = tmp_path / "cert.pem", tmp_path / "key.pem"
    cert_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert_path, key_path)
    requests = []

    async def handle(reader, writer):
        try:
            request = await reader.readuntil(b"\r\n\r\n")
            requests.append(request)
            body = (
                b'<AssetType name="Story"/>' if b"/meta.v1/" in request else b'<Assets total="0"/>'
            )
            writer.write(
                b"HTTP/1.1 200 OK\r\nContent-Type: application/xml\r\nContent-Length: "
                + str(len(body)).encode()
                + b"\r\nConnection: close\r\n\r\n"
                + body
            )
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    async with await asyncio.start_server(handle, "127.0.0.1", 0, ssl=context) as server:
        yield f"https://127.0.0.1:{server.sockets[0].getsockname()[1]}/instance", requests


@pytest.mark.parametrize("verify_tls", [True, False])
async def test_self_signed_certificate_requires_explicit_opt_out(self_signed_agility, verify_tls):
    base_url, requests = self_signed_agility
    config = load_config({"baseUrl": base_url, "verifyTls": verify_tls, "retries": 0}, {})
    async with create_http_client(config) as http:
        client = AgilityClient(config, Store(), http=http)
        if verify_tls:
            with pytest.raises(AgilityError, match="TLS trust"):
                await client.metadata(assetType="Story")
            assert not requests
        else:
            assert (await client.metadata(assetType="Story"))["AssetType"]["@name"] == "Story"
            assert len(requests) == 1


async def test_server_and_doctor_use_file_tls_configuration(
    self_signed_agility, tmp_path, monkeypatch, capsys
):
    base_url, requests = self_signed_agility
    path = tmp_path / "agility.json"
    path.write_text(json.dumps({"baseUrl": base_url, "verifyTls": False, "retries": 0}))
    for name in list(os.environ):
        if name.startswith("AGILITY_"):
            monkeypatch.delenv(name)
    config = load_config({"config": str(path)})
    async with Client(create_server(config, store=Store())) as client:
        result = await client.call_tool("agility_metadata", {"assetType": "Story"})
        assert result.structured_content["data"]["AssetType"]["@name"] == "Story"
        status = await client.call_tool("agility_status", {})
        assert status.structured_content["data"]["verifyTls"] is False
    monkeypatch.setattr(cli, "WindowsCredentialStore", Store)
    await cli.run(["doctor", "--config", str(path)])
    assert "query succeeded" in capsys.readouterr().err
    assert any(b"Authorization: Bearer synthetic-tls-token" in request for request in requests)


@pytest.mark.skipif(
    not os.environ.get("AGILITY_OFFLINE_LAUNCHER"),
    reason="Set AGILITY_OFFLINE_LAUNCHER to an extracted bundle",
)
async def test_offline_launcher_uses_tls_configuration(self_signed_agility, tmp_path):
    base_url, _ = self_signed_agility
    environment = {
        name: value for name, value in os.environ.items() if not name.startswith("AGILITY_")
    }
    environment["NO_PROXY"] = "localhost,127.0.0.1"
    for verify_tls in (True, False):
        path = tmp_path / "offline-agility.json"
        path.write_text(json.dumps({"baseUrl": base_url, "verifyTls": verify_tls, "retries": 0}))
        transport = StdioTransport(
            command=os.environ["AGILITY_OFFLINE_PYTHON"],
            args=[
                "-I",
                "-S",
                os.environ["AGILITY_OFFLINE_LAUNCHER"],
                "serve",
                "--config",
                str(path),
            ],
            env=environment,
        )
        async with Client(transport, timeout=30) as client:
            result = await client.call_tool(
                "agility_metadata", {"assetType": "Story"}, raise_on_error=False
            )
            assert result.is_error is verify_tls
            if not verify_tls:
                assert result.structured_content["data"]["AssetType"]["@name"] == "Story"
