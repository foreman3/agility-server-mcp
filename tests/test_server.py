import asyncio
import json
import os
import sys

import pytest
from conftest import FIXTURES, Store
from fastmcp import Client
from fastmcp.client.transports import StdioTransport

from agility_mcp.config import load_config
from agility_mcp.errors import AgilityError
from agility_mcp.server import create_server


def config(**overrides):
    return load_config({"baseUrl": "https://example.test/v1", **overrides}, {})


class Fake:
    def __init__(self):
        self.calls = []

    async def list(self, **args):
        self.calls.append(args)
        return {"assets": [], "nextStart": None}

    async def operation(self, **args):
        return {"operation": args["operation"]}

    async def query(self, **args):
        return args

    async def detail(self, **args):
        assert args["maxNodes"] == 100 and args["dependencyDepth"] == 1
        return {"item": {"oid": args["oid"]}, "traversal": {"truncated": False}}

    async def related(self, **args):
        return {"relation": args["relation"], "start": args["start"]}

    async def get(self, **args):
        raise AgilityError("Agility HTTP 401. Check credential.")


async def test_handshake_discovery_strict_arguments_and_envelope():
    fake = Fake()
    async with Client(create_server(config(), store=Store(), client=fake)) as client:
        tools = await client.list_tools()
        assert len(tools) == 9 and all(t.annotations.read_only_hint for t in tools)
        result = await client.call_tool("agility_status", {})
        assert result.structured_content["data"]["credential"]["found"]
        assert json.loads(result.content[0].text) == result.structured_content["data"]
        for args in (
            {"assetType": "Story", "pageSize": 1001},
            {"assetType": "Story", "op": "Delete"},
            {"assetType": "Story", "pageSize": "1"},
        ):
            result = await client.call_tool("agility_list_assets", args, raise_on_error=False)
            assert result.is_error and not fake.calls
        result = await client.call_tool("agility_list_assets", {"assetType": "Story"})
        assert result.structured_content == {"data": {"assets": [], "nextStart": None}}
        assert fake.calls[0]["pageSize"] == 100


async def test_all_tool_schemas_match_javascript_baseline():
    expected = json.loads((FIXTURES / "tools.json").read_text())
    async with Client(
        create_server(
            config(enableWrites=True, allowOperations=["Close", "Delete"]),
            store=Store(),
            client=Fake(),
        )
    ) as client:
        actual = await client.list_tools()
        assert len(actual) == len(expected) == 12
        for tool, baseline in zip(actual, expected):
            assert tool.name == baseline["name"]
            assert tool.description == baseline["description"]
            assert tool.input_schema == baseline["inputSchema"]
            assert (
                tool.annotations.model_dump(by_alias=True, exclude_none=True)
                == baseline["annotations"]
            )


async def test_conditional_writes_and_operations():
    async with Client(
        create_server(
            config(enableWrites=True, allowOperations=["Close"]), store=Store(), client=Fake()
        )
    ) as client:
        tools = await client.list_tools()
        assert len(tools) == 12
        operation = next(t for t in tools if t.name == "agility_execute_operation")
        assert operation.annotations.destructive_hint
        assert operation.input_schema["properties"]["operation"]["enum"] == ["Close"]
        assert (
            await client.call_tool(
                operation.name, {"oid": "Story:1", "operation": "Delete"}, raise_on_error=False
            )
        ).is_error
        assert (
            await client.call_tool(operation.name, {"oid": "Story:1", "operation": "Close"})
        ).structured_content["data"]["operation"] == "Close"
        for relation in (
            dict(name="Scope", action="set", targets=["Scope:1", "Scope:2"]),
            dict(name="Owners", action="add", targets=[]),
        ):
            assert (
                await client.call_tool(
                    "agility_update_asset",
                    {"oid": "Story:1", "relations": [relation]},
                    raise_on_error=False,
                )
            ).is_error
    async with Client(
        create_server(config(enableWrites=True), store=Store(), client=Fake())
    ) as client:
        assert len(await client.list_tools()) == 11


async def test_query_defaults_and_endpoint_injection():
    async with Client(create_server(config(), store=Store(), client=Fake())) as client:
        result = await client.call_tool(
            "agility_query",
            {
                "queries": [
                    {"from": "Story", "select": ["Name", {"from": "Owners", "select": ["Name"]}]}
                ]
            },
        )
        assert result.structured_content["data"]["queries"][0]["page"] == {"size": 100, "start": 0}
        assert (
            await client.call_tool(
                "agility_query",
                {"queries": [{"from": "Story", "op": "Delete"}]},
                raise_on_error=False,
            )
        ).is_error


async def test_errors_do_not_break_connection_and_are_sanitized():
    fake = Fake()
    async with Client(create_server(config(), store=Store(), client=fake)) as client:
        failure = await client.call_tool(
            "agility_get_asset", {"oid": "Story:1"}, raise_on_error=False
        )
        assert failure.is_error and "401" in failure.content[0].text
        assert len(await client.list_tools()) == 9

    class Unsafe(Fake):
        async def get(self, **args):
            raise RuntimeError("synthetic secret internal transport")

    async with Client(create_server(config(), store=Store(), client=Unsafe())) as client:
        failure = await client.call_tool(
            "agility_get_asset", {"oid": "Story:1"}, raise_on_error=False
        )
        assert failure.is_error and "synthetic" not in failure.content[0].text


async def test_graph_defaults_limits_and_continuations():
    async with Client(create_server(config(), store=Store(), client=Fake())) as client:
        for args in ({"oid": "Story:10", "maxNodes": 501}, {"oid": "Story:10:11"}):
            assert (
                await client.call_tool("agility_get_item_detail", args, raise_on_error=False)
            ).is_error
        assert (
            await client.call_tool("agility_get_item_detail", {"oid": "Story:10"})
        ).structured_content["data"]["item"]["oid"] == "Story:10"
        assert (
            await client.call_tool(
                "agility_related_items",
                {"oid": "Story:10", "relation": "Dependencies", "start": 50},
            )
        ).structured_content["data"]["start"] == 50


@pytest.mark.skipif(sys.platform != "win32", reason="Windows stdio server")
async def test_real_stdio_subprocess_local_fixture():
    seen = []

    async def handler(reader, writer):
        request = await reader.readuntil(b"\r\n\r\n")
        seen.append(request)
        body = b'<AssetType name="Story"><AttributeDefinition name="Name"/></AssetType>'
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
        env = {key: value for key, value in os.environ.items() if not key.startswith("AGILITY_")}
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
            ],
            env=env,
        )
        async with Client(transport, timeout=15) as client:
            assert len(await client.list_tools()) == 9
            result = await client.call_tool("agility_metadata", {"assetType": "Story"})
            assert result.structured_content["data"]["AssetType"]["@name"] == "Story"
        assert len(seen) == 1 and seen[0].startswith(b"GET /instance/meta.v1/Story ")
        assert b"authorization:" not in seen[0].lower()


async def test_mcp_cancellation_cancels_http(make_client):
    entered, cancelled = asyncio.Event(), asyncio.Event()

    async def wait(request):
        entered.set()
        try:
            await asyncio.sleep(20)
        finally:
            cancelled.set()

    active = make_client(wait=wait)
    async with Client(create_server(config(), store=Store(), client=active)) as client:
        task = asyncio.create_task(client.call_tool("agility_get_asset", {"oid": "Story:10"}))
        await asyncio.wait_for(entered.wait(), 5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await asyncio.wait_for(cancelled.wait(), 5)
