"""Explicit synthetic Windows vault integration; never reads tenant credentials."""

import asyncio
import json
import os
import sys
from uuid import uuid4

import pytest
from fastmcp import Client
from fastmcp.client.transports import StdioTransport

from agility_mcp.credentials import WindowsCredentialStore
from agility_mcp.errors import AgilityError

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows credential vault")


async def test_unicode_rotation_deletion_and_authenticated_stdio():
    target = "agility-mcp-test:" + str(uuid4())
    store = WindowsCredentialStore()
    seen = []
    try:
        assert not (await store.status(target))["found"]
        secret = "synthetic-only-✓-密码-🔒"
        await store.set(target, "test-user", secret)
        assert (await store.status(target))["found"]
        assert await store.get(target) == {"username": "test-user", "secret": secret}
        await store.set(target, "test-user", "rotated-synthetic-token")
        assert (await store.get(target))["secret"] == "rotated-synthetic-token"

        async def handler(reader, writer):
            request = await reader.readuntil(b"\r\n\r\n")
            seen.append(request)
            body = b'<Assets total="1"><Asset id="Story:10"><Attribute name="Name">Vault authenticated</Attribute><Attribute name="Super.Name">Parent feature</Attribute><Attribute name="Super.Number">E-1</Attribute><Relation name="Super"><Asset idref="Epic:1"/></Relation></Asset></Assets>'
            writer.write(
                b"HTTP/1.1 200 OK\r\nContent-Type: application/xml\r\nContent-Length: "
                + str(len(body)).encode()
                + b"\r\nConnection: close\r\n\r\n"
                + body
            )
            await writer.drain()
            writer.close()
            await writer.wait_closed()

        async with await asyncio.start_server(handler, "127.0.0.1", 0) as fixture:
            port = fixture.sockets[0].getsockname()[1]
            env = {
                key: value for key, value in os.environ.items() if not key.startswith("AGILITY_")
            }
            env["NO_PROXY"] = "127.0.0.1,localhost"
            transport = StdioTransport(
                command=sys.executable,
                args=[
                    "-m",
                    "agility_mcp",
                    "serve",
                    "--allow-http",
                    "--base-url",
                    f"http://127.0.0.1:{port}/instance",
                    "--credential-target",
                    target,
                ],
                env=env,
            )
            async with Client(transport, timeout=20) as client:
                result = await client.call_tool("agility_list_assets", {"assetType": "Story"})
                item = result.structured_content["data"]["assets"][0]
                assert item["attributes"]["Name"] == "Vault authenticated"
                assert item["parentId"] == "Epic:1" and item["parentName"] == "Parent feature"
                assert "rotated-synthetic-token" not in json.dumps(result.structured_content)
                assert all("rotated-synthetic-token" not in block.text for block in result.content)
            assert len(seen) == 1
            assert b"Authorization: Bearer rotated-synthetic-token\r\n" in seen[0]
        assert (await store.delete(target))["deleted"]
        assert not (await store.status(target))["found"]
        with pytest.raises(AgilityError, match="not found"):
            await store.get(target)
    finally:
        await store.delete(target)


async def test_real_worker_cancellation_reaps_process(monkeypatch):
    children = []
    original = asyncio.create_subprocess_exec

    async def spawn(*args, **kwargs):
        child = await original(sys.executable, "-c", "import time; time.sleep(60)", **kwargs)
        children.append(child)
        return child

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    task = asyncio.create_task(WindowsCredentialStore().status("agility-mcp-test:" + str(uuid4())))
    while not children:
        await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert children[0].returncode is not None
