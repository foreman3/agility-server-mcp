import asyncio
import json
from copy import deepcopy

import pytest
from conftest import BASE, FIXTURES

from agility_mcp.client import dumps
from agility_mcp.detail import schema_reader
from agility_mcp.errors import AgilityError

BASELINES = json.loads((FIXTURES / "detail-baseline.json").read_text())


@pytest.mark.parametrize(
    "baseline", BASELINES, ids=["detail", "portfolio", "cycles", "nodes", "edges", "requests"]
)
async def test_semantic_javascript_parity(make_client, baseline):
    client = make_client()
    result = await client.detail(**baseline["args"])
    del result["traversal"]["elapsedMs"]
    assert result == baseline["result"]


async def test_dependency_diamond_is_not_a_cycle(make_client):
    data = deepcopy(BASE["data"])
    data["Story:50"]["relations"]["Dependencies"] = []
    data["Defect:30"]["relations"]["Dependencies"] = ["Story:50"]
    result = await make_client(data=data).detail(oid="Story:10", dependencyDepth=4)
    assert result["graph"]["cycles"] == []
    assert (
        next(v for v in result["dependencies"]["upstream"] if v["oid"] == "Story:50")["distance"]
        == 2
    )


async def test_missing_reciprocal(make_client):
    schemas = BASE["metadata"] | {
        "PrimaryWorkitem": '<Base nameref="Workitem"/><AttributeDefinition name="Dependencies" attributetype="Relation"/>'
    }
    result = await make_client(schemas=schemas).detail(oid="Story:10")
    assert (
        next(v for v in result["coverage"] if v["direction"] == "upstream")["status"]
        == "unavailable"
    )
    assert "relationship_unavailable" in result["traversal"]["reasons"]


async def test_internal_deadline_keeps_partial_data(make_client):
    cancelled = asyncio.Event()

    async def wait(request):
        if request.url.params.get("where") == "Dependants='Story:10'":
            try:
                await asyncio.sleep(3)
            finally:
                cancelled.set()

    result = await make_client(wait=wait).detail(oid="Story:10", maxDurationMs=1000)
    assert result["item"]["parentName"] == "Feature"
    assert "maxDurationMs" in result["traversal"]["reasons"]
    assert result["traversal"]["pending"]
    assert cancelled.is_set()


async def test_output_limit_keeps_continuations(make_client):
    result = await make_client(overrides={"maxResponseBytes": 6500}).detail(
        oid="Story:10", pageSize=1, dependencyDepth=3
    )
    assert len(dumps(result).encode()) <= 6500
    assert "maxResponseBytes" in result["traversal"]["reasons"]
    assert result["traversal"]["pending"]


async def test_permission_failure_distinct_from_empty(make_client):
    result = await make_client(
        fail=lambda req: 403 if req.url.params.get("where") == "Dependencies='Story:10'" else None
    ).detail(oid="Story:10")
    assert not result["traversal"]["completeWithinRequestedDepth"]
    assert any(
        v["direction"] == "downstream" and v["status"] == "error" and "403" in v["message"]
        for v in result["coverage"]
    )
    assert len(result["dependencies"]["upstream"]) == 2


async def test_missing_metadata_is_incomplete(make_client):
    result = await make_client(
        fail=lambda req: 403 if "/meta.v1/" in req.url.path else None
    ).detail(oid="Story:10")
    assert result["item"]["parentId"] == "Epic:2"
    assert result["metadataStatus"] == "unavailable"
    assert "metadata_unavailable" in result["traversal"]["reasons"]


async def test_related_pages(make_client):
    client = make_client()
    first = await client.related(oid="Story:10", relation="Dependencies", pageSize=1)
    assert first["assets"][0]["parentName"] == "Feature"
    assert first["continuation"]["arguments"] == dict(
        oid="Story:10", relation="Dependencies", pageSize=1, start=1
    )
    second = await client.related(**first["continuation"]["arguments"])
    assert second["assets"][0]["oid"] == "Defect:30"
    assert second["continuation"] is None
    assert (await client.related(oid="Story:10", relation="Subs"))["status"] == "unsupported"
    with pytest.raises(AgilityError, match="404"):
        await client.related(oid="Story:999", relation="Dependencies")


async def test_invalid_detail_and_inheritance(make_client):
    bad = make_client(schemas=BASE["metadata"] | {"Story": '<Base nameref="Story"/>'})
    with pytest.raises(AgilityError, match="inheritance"):
        await schema_reader(bad)("Story")
    client = make_client()
    for args in ({"oid": "Story:10:5"}, {"oid": "Story:10", "maxNodes": 0}):
        with pytest.raises(AgilityError):
            await client.detail(**args)
    with pytest.raises(AgilityError, match="Invalid"):
        await client.related(oid="Story:10", relation="Dependencies?op=Delete")


async def test_external_cancellation_propagates(make_client):
    entered = asyncio.Event()

    async def wait(request):
        if request.url.params.get("where"):
            entered.set()
            await asyncio.sleep(10)

    task = asyncio.create_task(make_client(wait=wait).detail(oid="Story:10"))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
