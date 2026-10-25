import asyncio
import base64
import json

import httpx
import pytest

from agility_mcp.config import load_config
from agility_mcp.credentials import WindowsCredentialStore, authorization
from agility_mcp.errors import AgilityError

XML = '<Assets total="2"><Asset id="Story:1"><Attribute name="Name">First</Attribute></Asset></Assets>'


async def test_bearer_url_filters_paging(make_client):
    async def handler(request):
        assert request.url.path == "/v1/rest-1.v1/Data/Story"
        assert request.url.params["page"] == "1,0"
        assert request.url.params["where"] == "Name='A&B'"
        assert request.headers["Authorization"] == "Bearer synthetic"
        return httpx.Response(200, text=XML)

    assert (await make_client(handler).list(assetType="Story", where="Name='A&B'", pageSize=1))[
        "nextStart"
    ] == 1


async def test_short_pages_and_stalls(make_client):
    responses = [
        '<Assets total="3"><Asset id="Story:1"/></Assets>',
        '<Assets total="3"><Asset id="Story:2"/></Assets>',
        '<Assets total="3"/>',
    ]

    async def handler(request):
        return httpx.Response(200, text=responses.pop(0))

    client = make_client(handler)
    first = await client.list(assetType="Story", pageSize=50)
    second = await client.list(assetType="Story", pageSize=50, start=first["nextStart"])
    stalled = await client.list(assetType="Story", pageSize=50, start=second["nextStart"])
    assert first["nextStart"] == 1 and second["nextStart"] == 2
    assert stalled["nextStart"] is None and stalled["paginationStatus"] == "stalled"


async def test_short_page_without_total(make_client):
    responses = ['<Assets><Asset id="Story:1"/></Assets>', "<Assets/>"]

    async def handler(request):
        return httpx.Response(200, text=responses.pop(0))

    client = make_client(handler)
    assert (await client.list(assetType="Story"))["nextStart"] == 1
    assert (await client.list(assetType="Story", start=1))["nextStart"] is None


async def test_credential_rotation_basic_and_controls(make_client):
    async def handler(request):
        return httpx.Response(200, text=XML)

    client = make_client(handler)
    await client.list(assetType="Story")
    await client.list(assetType="Story")
    assert client.store.reads == 2
    cfg = load_config({"baseUrl": "https://example.test", "authMode": "basic"}, {})
    assert (
        await authorization(client.store, cfg)
        == "Basic " + base64.b64encode(b"demo:synthetic").decode()
    )

    class Invalid:
        async def get(self, target):
            return {"username": "", "secret": "x\ny"}

    with pytest.raises(AgilityError, match="control"):
        await authorization(Invalid(), cfg)
    with pytest.raises(AgilityError, match="Windows"):
        await WindowsCredentialStore(platform="linux").get("target")


async def test_metadata_anonymous_instance_path(make_client):
    async def handler(request):
        assert request.url.path == "/v1/meta.v1/Story/Name"
        assert "authorization" not in request.headers
        return httpx.Response(200, text='<AssetType name="Story"/>')

    assert (await make_client(handler).metadata(assetType="Story", member="Name"))["AssetType"][
        "@name"
    ] == "Story"


async def test_query_readonly_post_retries_and_parent_selection(make_client):
    calls = 0

    async def handler(request):
        nonlocal calls
        calls += 1
        assert request.method == "POST" and request.url.path == "/v1/query.v1"
        selected = json.loads(request.content)[0]["select"]
        assert "Super.Name" in selected and "Super" not in selected
        assert len([v for v in selected if isinstance(v, dict) and v["from"] == "Super"]) == 1
        return (
            httpx.Response(429, headers={"retry-after": "0"})
            if calls == 1
            else httpx.Response(
                200, json=[[{"_oid": "Story:10", "Super": {"_oid": "Epic:1", "Name": "Feature"}}]]
            )
        )

    result = await make_client(handler, overrides={"retries": 1}).query(
        queries=[{"from": "Story", "select": ["Name", {"from": "Super", "select": ["Name"]}]}]
    )
    assert calls == 2 and result[0][0]["parentId"] == "Epic:1"


async def test_mutations_no_retries_and_allowlist(make_client):
    calls = 0

    async def handler(request):
        nonlocal calls
        calls += 1
        return httpx.Response(503, text="synthetic private details")

    client = make_client(
        handler, overrides={"enableWrites": True, "allowOperations": ["Close"], "retries": 5}
    )
    with pytest.raises(AgilityError, match="503") as error:
        await client.mutate(assetType="Story", attributes={"Name": "Hello"})
    assert "synthetic" not in str(error.value) and calls == 1
    with pytest.raises(AgilityError, match="allowOperations"):
        await client.operation(oid="Story:1", operation="Delete")
    with pytest.raises(AgilityError, match="disabled"):
        await make_client().mutate(assetType="Story", attributes={"Name": "Hello"})
    assert calls == 1


async def test_successful_mutation_oauth_and_operation(make_client):
    async def handler(request):
        assert "/rest-1.oauth.v1/Data/Story" in request.url.path
        return httpx.Response(
            200, text='<Asset id="Story:5:8"><Attribute name="Name">Created</Attribute></Asset>'
        )

    client = make_client(
        handler,
        overrides={
            "enableWrites": True,
            "allowOperations": ["Close"],
            "restEndpoint": "rest-1.oauth.v1",
        },
    )
    result = await client.mutate(
        assetType="Story",
        attributes={"Name": "Created"},
        relations=[dict(name="Scope", action="set", targets=["Scope:0"])],
    )
    assert result["assets"][0]["oid"] == "Story:5:8"
    assert b'<Relation name="Scope" act="set">' in client.requests[0].content
    await client.operation(oid="Story:5", operation="Close")
    assert client.requests[-1].url.params["op"] == "Close"


@pytest.mark.parametrize("status", [302, 401, 403, 404, 500])
async def test_http_errors_redacted_redirects_blocked(make_client, status):
    async def handler(request):
        return httpx.Response(
            status, text="synthetic private details", headers={"location": "https://elsewhere.test"}
        )

    client = make_client(handler)
    with pytest.raises(AgilityError) as error:
        await client.get(oid="Story:1")
    assert error.value.status == status and "synthetic" not in str(error.value)
    assert len(client.requests) == 1


class Stream(httpx.AsyncByteStream):
    async def __aiter__(self):
        yield b"x" * 800
        yield b"x" * 800


async def test_stream_json_and_request_limits(make_client):
    async def handler(request):
        return httpx.Response(200, stream=Stream())

    client = make_client(handler, overrides={"maxResponseBytes": 1024})
    with pytest.raises(AgilityError, match="exceeds"):
        await client.get(oid="Story:1")
    with pytest.raises(AgilityError, match="Request body"):
        await client.query(queries=[{"x": "x" * 1048576}])

    async def invalid(request):
        return httpx.Response(200, text="<html>login</html>")

    with pytest.raises(AgilityError, match="invalid JSON"):
        await make_client(invalid).query(queries=[])


async def test_timeout_cancellation_and_transport_redaction(make_client):
    entered = asyncio.Event()

    async def handler(request):
        entered.set()
        await asyncio.sleep(10)

    client = make_client(handler, overrides={"timeoutMs": 100})
    with pytest.raises(AgilityError, match="timed out"):
        await client.get(oid="Story:1")
    entered.clear()
    task = asyncio.create_task(client.get(oid="Story:1"))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    async def failure(request):
        raise httpx.ConnectError("synthetic secret details")

    with pytest.raises(AgilityError, match="TLS trust") as error:
        await make_client(failure).get(oid="Story:1")
    assert "synthetic" not in str(error.value)


async def test_concurrency_and_cancelled_queue(make_client):
    active = maximum = 0

    async def handler(request):
        nonlocal active, maximum
        active += 1
        maximum = max(maximum, active)
        await asyncio.sleep(0.01)
        active -= 1
        return httpx.Response(200, text=XML)

    client = make_client(handler, overrides={"maxConcurrency": 2})
    await asyncio.gather(*(client.list(assetType="Story") for _ in range(6)))
    assert maximum == 2
    release, entered = asyncio.Event(), asyncio.Event()

    async def blocked(request):
        entered.set()
        await release.wait()
        return httpx.Response(200, text=XML)

    client = make_client(blocked, overrides={"maxConcurrency": 1})
    first = asyncio.create_task(client.list(assetType="Story"))
    await entered.wait()
    queued = asyncio.create_task(client.list(assetType="Story"))
    await asyncio.sleep(0)
    queued.cancel()
    with pytest.raises(asyncio.CancelledError):
        await queued
    release.set()
    await first
    await client.list(assetType="Story")
    assert len(client.requests) == 2 and client.limiter.waiting == 0


async def test_ambiguity_empty_success_and_enrichment_failure(make_client):
    async def invalid(request):
        return httpx.Response(200, text="<html>unexpected</html>")

    with pytest.raises(AgilityError, match="Verify the asset before retrying"):
        await make_client(invalid, overrides={"enableWrites": True}).mutate(
            assetType="Story", attributes={"Name": "Hello"}
        )

    async def empty(request):
        return httpx.Response(204)

    assert await make_client(empty, overrides={"enableWrites": True}).mutate(
        oid="Story:1", attributes={"Name": "Hello"}
    ) == {"completed": True}

    async def enrich(request):
        return (
            httpx.Response(
                200, text='<Asset id="Story:10"><Attribute name="Name">Saved</Attribute></Asset>'
            )
            if request.method == "POST"
            else httpx.Response(403)
        )

    client = make_client(enrich, overrides={"enableWrites": True})
    result = await client.mutate(oid="Story:10", attributes={"Name": "Saved"})
    assert result["assets"][0]["attributes"]["Name"] == "Saved"
    assert "do not repeat" in result["hierarchyWarnings"][0]
    assert len([r for r in client.requests if r.method == "POST"]) == 1


async def test_parents_on_list_get_history(make_client):
    async def handler(request):
        assert request.url.params["sel"] == "Description,Super,Super.Name,Super.Number"
        return httpx.Response(
            200,
            text='<Assets total="1"><Asset id="Story:10"><Attribute name="Super.Name">Feature</Attribute><Relation name="Super"><Asset idref="Epic:1"/></Relation></Asset></Assets>',
        )

    client = make_client(handler)
    for result in (
        await client.list(assetType="Story", select=["Description"]),
        await client.get(oid="Story:10", select=["Description"]),
        await client.history(oid="Story:10", select=["Description"]),
    ):
        assert result["assets"][0]["parentName"] == "Feature"


async def test_queue_limit_and_retry_wait_cancellation(make_client):
    entered = asyncio.Event()

    async def handler(request):
        entered.set()
        return httpx.Response(429, headers={"retry-after": "30"})

    client = make_client(handler, overrides={"retries": 1, "maxConcurrency": 1})
    task = asyncio.create_task(client.get(oid="Story:1"))
    await entered.wait()
    queued = [asyncio.create_task(client.get(oid="Story:1")) for _ in range(64)]
    await asyncio.sleep(0)
    with pytest.raises(AgilityError, match="Too many pending"):
        await client.get(oid="Story:1")
    for pending in [task, *queued]:
        pending.cancel()
    await asyncio.gather(task, *queued, return_exceptions=True)
    assert client.limiter.waiting == 0 and len(client.requests) == 1
