"""Opt-in read-only tenant checks."""

import os
import sys

import pytest

from agility_mcp.client import AgilityClient, create_http_client
from agility_mcp.config import load_config
from agility_mcp.credentials import WindowsCredentialStore
from agility_mcp.items import canonical_oid
from agility_mcp.xml import oid_path

pytestmark = pytest.mark.skipif(
    os.environ.get("AGILITY_LIVE_TEST") != "1" or sys.platform != "win32",
    reason="Set AGILITY_LIVE_TEST=1 on Windows to opt in to tenant reads",
)


async def test_live_readonly():
    oid = os.environ["AGILITY_LIVE_ITEM_OID"]
    oid_path(oid, False)
    config = load_config({"enableWrites": False, "allowOperations": []})
    async with create_http_client(config) as http:
        client = AgilityClient(config, WindowsCredentialStore(), http=http)
        assert (await client.metadata(assetType=oid.split(":")[0]))["AssetType"]
        result = await client.get(oid=oid, select=["Name", "Number"])
        assert any(canonical_oid(item.get("oid")) == oid for item in result["assets"])
        detail = await client.detail(
            oid=oid,
            hierarchyDepth=1,
            dependencyDepth=1,
            pageSize=20,
            maxNodes=50,
            maxEdges=100,
            maxRequests=60,
            maxDurationMs=30000,
        )
        assert canonical_oid(detail["item"]["oid"]) == oid
        assert isinstance(detail["graph"]["edges"], list)
        assert isinstance(detail["impact"]["affected"], list)
        assert isinstance(detail["traversal"]["reasons"], list)
        if parent := os.environ.get("AGILITY_LIVE_EXPECT_PARENT_OID"):
            assert detail["item"]["parentId"] == parent
        for direction in ("UPSTREAM", "DOWNSTREAM"):
            if expected := os.environ.get(f"AGILITY_LIVE_EXPECT_{direction}_OID"):
                assert any(
                    value["oid"] == expected for value in detail["dependencies"][direction.lower()]
                )
        if relation := os.environ.get("AGILITY_LIVE_RELATION"):
            assert (await client.related(oid=oid, relation=relation, pageSize=20))["status"] in (
                "ok",
                "unsupported",
                "unavailable",
            )
        print({"oid": oid, "reasons": detail["traversal"]["reasons"]})
