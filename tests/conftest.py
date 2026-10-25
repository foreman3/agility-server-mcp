import json
import re
from copy import deepcopy
from pathlib import Path

import httpx
import pytest

from agility_mcp.client import AgilityClient
from agility_mcp.config import load_config
from agility_mcp.xml import escape_xml

FIXTURES = Path(__file__).parent / "fixtures"
BASE = json.loads((FIXTURES / "agility.json").read_text())


class Store:
    def __init__(self):
        self.reads = 0

    async def get(self, target):
        self.reads += 1
        return {"username": "demo", "secret": "synthetic"}

    async def status(self, target):
        return {"found": True}


def xml_asset(oid, data, selected):
    row = data[oid]
    parts = []
    for field in selected:
        if field == "Super":
            members = "".join(
                f'<Asset idref="{parent}"><Attribute name="Name">{escape_xml(data.get(parent, {}).get("Name", ""))}</Attribute></Asset>'
                for parent in row["relations"].get("Super", [])
            )
            parts.append(f'<Relation name="Super">{members}</Relation>')
        elif field.startswith("Super."):
            parent = next(iter(row["relations"].get("Super", [])), None)
            parts.append(
                f'<Attribute name="{field}">{escape_xml(data.get(parent, {}).get(field[6:], ""))}</Attribute>'
            )
        else:
            parts.append(f'<Attribute name="{field}">{escape_xml(row.get(field, ""))}</Attribute>')
    return f'<Asset id="{oid}">{"".join(parts)}</Asset>'


@pytest.fixture
async def make_client():
    clients = []

    def make(handler=None, *, overrides=None, data=None, schemas=None, fail=None, wait=None):
        config = load_config(
            {"baseUrl": "https://example.test/v1", "retries": 0, **(overrides or {})}, {}
        )
        store = Store()
        requests = []
        rows = deepcopy(BASE["data"] if data is None else data)
        meta = BASE["metadata"] if schemas is None else schemas
        for oid, row in list(rows.items()):
            for relation, reciprocal in (
                ("Super", "Subs"),
                ("Dependencies", "Dependants"),
                ("BlockingIssues", "BlockedPrimaryWorkitems"),
            ):
                for target in list(row["relations"].get(relation, [])):
                    if target in rows:
                        rows[target]["relations"].setdefault(reciprocal, []).append(oid)

        async def fixture(request):
            requests.append(request)
            if handler:
                return await handler(request)
            if wait:
                await wait(request)
            if fail and (status := fail(request)):
                return httpx.Response(status, text="synthetic private error")
            path = request.url.path
            if "/meta.v1/" in path:
                asset_type = path.split("/")[-1]
                return (
                    httpx.Response(
                        200,
                        text=f'<AssetType name="{asset_type}" version="test">{meta[asset_type]}</AssetType>',
                    )
                    if asset_type in meta
                    else httpx.Response(404)
                )
            parts = path.split("/")
            asset_type = parts[4]
            selected = request.url.params.get("sel", "Name").split(",")
            if len(parts) > 5:
                oid = f"{asset_type}:{parts[5]}"
                return (
                    httpx.Response(200, text=xml_asset(oid, rows, selected))
                    if oid in rows
                    else httpx.Response(404)
                )
            match = re.fullmatch(
                r"([A-Za-z][A-Za-z0-9_]*)='([A-Za-z][A-Za-z0-9_]*:[0-9]+)'",
                request.url.params.get("where", ""),
            )
            assert match
            matches = []
            for oid, row in rows.items():
                concrete = oid.split(":")[0]
                valid = (
                    concrete == asset_type
                    or asset_type == "Workitem"
                    and concrete in ("Epic", "Story", "Defect")
                    or asset_type == "PrimaryWorkitem"
                    and concrete in ("Story", "Defect")
                )
                if valid and match[2] in row["relations"].get(match[1], []):
                    matches.append(oid)
            size, start = map(int, request.url.params["page"].split(","))
            return httpx.Response(
                200,
                text=f'<Assets total="{len(matches)}">'
                + "".join(xml_asset(oid, rows, selected) for oid in matches[start : start + size])
                + "</Assets>",
            )

        http = httpx.AsyncClient(transport=httpx.MockTransport(fixture), timeout=None)
        clients.append(http)
        client = AgilityClient(config, store, http=http)
        client.requests = requests
        return client

    yield make
    for client in clients:
        await client.aclose()
