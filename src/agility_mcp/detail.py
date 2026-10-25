"""Bounded hierarchy/dependency traversal with explicit partial coverage."""

import asyncio
import re
from collections.abc import Awaitable, Callable
from typing import Any

from .client import AgilityClient, before_attempt, dumps
from .errors import AgilityError
from .items import canonical_oid, describe_metadata, item_summary, unique, with_parent
from .xml import assert_name, oid_path

SCALAR_DEFAULTS = [
    "Name",
    "Number",
    "Description",
    "Reference",
    "Estimate",
    "ToDo",
    "AssetState",
    "CreateDate",
    "ChangeDate",
    "ChangeDateUTC",
    "PlannedStart",
    "PlannedEnd",
]
SINGLE_RELATIONS = [
    "Scope",
    "Status",
    "Priority",
    "Team",
    "Timebox",
    "Category",
    "Parent",
    "CreatedBy",
    "ChangedBy",
]
SchemaReader = Callable[..., Awaitable[dict[str, Any]]]


class Limit(AgilityError):
    def __init__(self, reason: str):
        super().__init__(f"Detailed item traversal stopped: {reason}.")
        self.reason = reason


def check_integer(value: int, low: int, high: int, label: str) -> None:
    if type(value) is not int or not low <= value <= high:
        raise AgilityError(f"{label} must be an integer between {low} and {high}.")


def schema_reader(client: AgilityClient) -> SchemaReader:
    cache: dict[str, Any] = {}

    async def read(asset_type: str, ancestry: tuple[str, ...] = ()) -> dict[str, Any]:
        assert_name(asset_type)
        if asset_type in ancestry or len(ancestry) >= 12:
            raise AgilityError("Invalid or excessively deep metadata inheritance.")
        if asset_type in cache:
            return cache[asset_type]
        own = describe_metadata(await client.metadata(assetType=asset_type), asset_type)
        base: dict[str, Any] = (
            await read(own["baseType"], (*ancestry, asset_type)) if own["baseType"] else {}
        )
        result = own | {
            "fields": base.get("fields", {}) | own["fields"],
            "operations": unique(base.get("operations", []) + own["operations"]),
            "inheritance": [asset_type] + base.get("inheritance", []),
        }
        cache[asset_type] = result
        return result

    return read


def select_for(
    schema: dict[str, Any], *, detailed: bool = False, select: list[str] | None = None
) -> list[str]:
    if select is not None:
        for field in select:
            if field.split(".")[0] not in schema["fields"]:
                raise AgilityError(f"Field {field} is not defined for {schema['assetType']}.")
        return unique(["Name"] + select)
    fields = (
        SCALAR_DEFAULTS
        + [
            field["name"]
            for field in schema["fields"].values()
            if field["custom"]
            and not field["multiValue"]
            and not re.search(
                r"relation|binary|blob|opaque|password", field.get("type", ""), re.IGNORECASE
            )
        ]
        if detailed
        else ["Name", "Number"]
    )
    fields = [
        field
        for field in fields
        if field in schema["fields"] and schema["fields"][field].get("type") != "Relation"
    ]
    if detailed:
        for field in SINGLE_RELATIONS:
            definition = schema["fields"].get(field, {})
            if definition.get("type") == "Relation" and not definition["multiValue"]:
                fields.extend([field, field + ".Name"])
    return unique(fields)[:100]


async def relation_page(
    client: AgilityClient,
    read_schema: SchemaReader,
    *,
    oid: str,
    relation: str,
    pageSize: int = 50,
    start: int = 0,
) -> dict[str, Any]:
    oid_path(oid, False)
    assert_name(relation)
    schema = await read_schema(oid.split(":")[0])
    definition = schema["fields"].get(relation, {})
    base: dict[str, Any] = {"oid": oid, "relation": relation, "assets": [], "nextStart": None}
    if definition.get("type") != "Relation":
        return base | {
            "status": "unsupported",
            "reason": "Relationship is not defined by this instance metadata.",
        }
    if not definition.get("targetType") or not definition.get("reciprocal"):
        return base | {
            "status": "unavailable",
            "reason": "Metadata does not supply a target type and reciprocal relationship for safe paging.",
        }
    target = await read_schema(definition["targetType"])
    reciprocal = target["fields"].get(definition["reciprocal"], {})
    if (
        reciprocal.get("type") != "Relation"
        or reciprocal.get("targetType")
        and reciprocal["targetType"] not in schema["inheritance"]
    ):
        return base | {
            "status": "unavailable",
            "reason": "Reciprocal relationship cannot be verified in target metadata.",
        }
    result = await client.list(
        assetType=definition["targetType"],
        select=select_for(target),
        where=f"{definition['reciprocal']}='{oid}'",
        sort=["ID"],
        pageSize=pageSize,
        start=start,
    )
    return {"oid": oid, "relation": relation, "status": "ok"} | result


def continuation(oid: str, relation: str, page_size: int, start: int) -> dict[str, Any]:
    return {
        "tool": "agility_related_items",
        "arguments": {"oid": oid, "relation": relation, "pageSize": page_size, "start": start},
    }


async def related_items(
    client: AgilityClient, *, oid: str, relation: str, pageSize: int = 50, start: int = 0
) -> dict[str, Any]:
    check_integer(pageSize, 1, 200, "pageSize")
    check_integer(start, 0, 9007199254740991, "start")
    oid_path(oid, False)
    assert_name(relation)
    source = await client.get(oid=oid, select=["Name"])
    if not any(canonical_oid(asset.get("oid")) == oid for asset in source["assets"]):
        raise AgilityError("The requested source item was not returned by Agility.")
    result = await relation_page(
        client, schema_reader(client), oid=oid, relation=relation, pageSize=pageSize, start=start
    )
    return result | {
        "permissionScope": "visible_to_current_user",
        "continuation": None
        if result["nextStart"] is None
        else continuation(oid, relation, pageSize, result["nextStart"]),
    }


def cycles(
    nodes: list[dict[str, Any]], edges: list[dict[str, Any]], kind: str
) -> list[dict[str, Any]]:
    adjacency: dict[str, list[str]] = {node["oid"]: [] for node in nodes}
    for edge in edges:
        if edge["kind"] == kind and edge["from"] in adjacency:
            adjacency[edge["from"]].append(edge["to"])
    state: dict[str, int] = {}
    stack: list[str] = []
    found: list[dict[str, Any]] = []
    signatures = set()

    def visit(oid: str) -> None:
        state[oid] = 1
        stack.append(oid)
        for target in adjacency.get(oid, []):
            if state.get(target) == 1:
                path = stack[stack.index(target) :]
                key = "|".join(sorted(path))
                if key not in signatures and len(found) < 25:
                    signatures.add(key)
                    found.append({"kind": kind, "path": path + [target]})
            elif target not in state:
                visit(target)
        stack.pop()
        state[oid] = 2

    for node in nodes:
        if node["oid"] not in state:
            visit(node["oid"])
    return found


def dependency_impact(root: str, edges: list[dict[str, Any]]) -> dict[str, Any]:
    forward: dict[str, set[str]] = {}
    backward: dict[str, set[str]] = {}
    for edge in edges:
        if edge["kind"] == "dependency":
            forward.setdefault(edge["from"], set()).add(edge["to"])
            backward.setdefault(edge["to"], set()).add(edge["from"])

    def paths(adjacency: dict[str, set[str]], reverse: bool = False) -> list[dict[str, Any]]:
        found = {root: [root]}
        queue = [root]
        for current in queue:
            for target in sorted(adjacency.get(current, set())):
                if target in found:
                    continue
                found[target] = [target] + found[current] if reverse else found[current] + [target]
                queue.append(target)
        del found[root]
        return sorted(
            [{"oid": oid, "distance": len(path) - 1, "path": path} for oid, path in found.items()],
            key=lambda v: (v["distance"], v["oid"]),
        )

    affected = paths(forward)
    return {
        "semantics": "Explicit dependency edges only; paths are evidence in graph.edges.",
        "affected": affected,
        "blockingChains": paths(backward, True),
        "directDependants": [value["oid"] for value in affected if value["distance"] == 1],
    }


async def get_item_detail(
    client: AgilityClient,
    *,
    oid: str,
    hierarchyDepth: int = 2,
    dependencyDepth: int = 1,
    pageSize: int = 50,
    maxNodes: int = 100,
    maxEdges: int = 500,
    maxRequests: int = 80,
    maxDurationMs: int = 30000,
    includeMetadata: bool = True,
    select: list[str] | None = None,
) -> dict[str, Any]:
    oid_path(oid, False)
    limits = dict(
        hierarchyDepth=hierarchyDepth,
        dependencyDepth=dependencyDepth,
        pageSize=pageSize,
        maxNodes=maxNodes,
        maxEdges=maxEdges,
        maxRequests=maxRequests,
        maxDurationMs=maxDurationMs,
    )
    for label, low, high in (
        ("hierarchyDepth", 0, 10),
        ("dependencyDepth", 0, 10),
        ("pageSize", 1, 200),
        ("maxNodes", 1, 500),
        ("maxEdges", 1, 5000),
        ("maxRequests", 5, 300),
        ("maxDurationMs", 1000, 120000),
    ):
        check_integer(limits[label], low, high, label)
    started = asyncio.get_running_loop().time()
    deadline = started + maxDurationMs / 1000
    requests = 0

    def count_attempt() -> None:
        nonlocal requests
        if asyncio.get_running_loop().time() >= deadline:
            raise Limit("maxDurationMs")
        if requests >= maxRequests:
            raise Limit("maxRequests")
        requests += 1

    token = before_attempt.set(count_attempt)
    try:
        return await _traverse(
            client, oid, limits, includeMetadata, select, started, deadline, lambda: requests
        )
    except TimeoutError:
        raise AgilityError(
            "Detailed item traversal stopped: maxDurationMs before loading the root."
        ) from None
    finally:
        before_attempt.reset(token)


async def _traverse(
    client: AgilityClient,
    oid: str,
    limits: dict[str, int],
    include_metadata: bool,
    select: list[str] | None,
    started: float,
    deadline: float,
    request_count: Callable[[], int],
) -> dict[str, Any]:
    hierarchy_depth, dependency_depth, page_size = (
        limits[key] for key in ("hierarchyDepth", "dependencyDepth", "pageSize")
    )
    max_nodes, max_edges = limits["maxNodes"], limits["maxEdges"]
    read_schema = schema_reader(client)
    warnings: list[dict[str, Any]] = []
    reasons: dict[str, None] = {}
    schema = None
    async with asyncio.timeout_at(deadline):
        try:
            schema = await read_schema(oid.split(":")[0])
        except Limit:
            raise
        except AgilityError as error:
            warnings.append({"code": "metadata_unavailable", "message": str(error)})
        root = await client.get(
            oid=oid,
            select=select_for(schema, detailed=True, select=select)
            if schema
            else select
            if select is not None
            else ["Name", "Number", "Description"],
        )
    item = next((asset for asset in root["assets"] if canonical_oid(asset.get("oid")) == oid), None)
    if item is None:
        raise AgilityError("The requested item was not returned by Agility.")
    if item.get("parentStatus") == "unavailable":
        reasons["parent_unavailable"] = None
    nodes = {oid: item_summary(item)}
    edges: dict[str, Any] = {}
    coverage: dict[str, Any] = {}
    queue: list[dict[str, Any]] = []
    output_limit = client.config.maxResponseBytes
    directions: list[dict[str, Any]] = [
        dict(
            key="ancestors",
            candidates=["Super"],
            kind="hierarchy",
            reverse=True,
            depth=hierarchy_depth,
        ),
        dict(
            key="descendants",
            candidates=["Subs"],
            kind="hierarchy",
            reverse=False,
            depth=hierarchy_depth,
        ),
        dict(
            key="upstream",
            candidates=["Dependencies", "UpstreamDependencies"],
            kind="dependency",
            reverse=True,
            depth=dependency_depth,
        ),
        dict(
            key="downstream",
            candidates=["Dependants", "Dependents", "DownstreamDependencies"],
            kind="dependency",
            reverse=False,
            depth=dependency_depth,
        ),
        dict(
            key="blockingIssues",
            candidates=["BlockingIssues"],
            kind="blocker",
            reverse=True,
            depth=1,
        ),
    ]
    reached: dict[str, dict[str, int]] = {direction["key"]: {} for direction in directions}
    expanded = set()
    page_seen: dict[str, set[str]] = {}
    omit_metadata = False
    omitted_warnings = 0

    def result_data(extra_pending: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        pending = [
            {
                "oid": task["source"],
                "direction": task["direction"]["key"],
                "relation": coverage.get(task["direction"]["key"] + "|" + task["source"], {}).get(
                    "relation", task["direction"]["candidates"][0]
                ),
                "start": task["start"],
            }
            for task in (extra_pending or []) + queue
        ]

        def group(key: str) -> list[dict[str, Any]]:
            return [
                nodes[target] | {"distance": distance} for target, distance in reached[key].items()
            ]

        node_list, edge_list = list(nodes.values()), list(edges.values())
        graph_cycles = cycles(node_list, edge_list, "hierarchy") + cycles(
            node_list, edge_list, "dependency"
        )
        complete = not reasons and not pending
        result = {
            "item": item,
            "metadataStatus": "omitted_for_size"
            if omit_metadata
            else "available"
            if schema
            else "unavailable",
            "hierarchy": {
                "parent": item.get("parent"),
                "ancestors": group("ancestors"),
                "descendants": group("descendants"),
            },
            "dependencies": {
                "semantics": "Edges point from prerequisite to dependent; upstream items must finish before this item, downstream items depend on it.",
                "scope": "explicit_relationships; implicit inherited constraints are not synthesized",
                "upstream": group("upstream"),
                "downstream": group("downstream"),
                "blockingIssues": group("blockingIssues"),
            },
            "impact": dependency_impact(oid, edge_list)
            | {
                "cycles": [cycle for cycle in graph_cycles if cycle["kind"] == "dependency"],
                "completeWithinRequestedDepth": complete,
            },
            "graph": {"nodes": node_list, "edges": edge_list, "cycles": graph_cycles},
            "coverage": list(coverage.values()),
            "warnings": warnings,
            "traversal": {
                "completeWithinRequestedDepth": complete,
                "truncated": not complete,
                "reasons": list(reasons),
                "pending": pending,
                "requests": request_count(),
                "elapsedMs": int((asyncio.get_running_loop().time() - started) * 1000),
                "permissionScope": "visible_to_current_user",
                "limits": limits,
                "consistency": "live_nontransactional; concurrent changes and account visibility can affect results",
                "depthBoundaries": [
                    {
                        "direction": direction["key"],
                        "depth": direction["depth"],
                        "unexpanded": [oid]
                        if direction["depth"] == 0
                        else [
                            target
                            for target, distance in reached[direction["key"]].items()
                            if distance == direction["depth"]
                        ],
                    }
                    for direction in directions
                    if direction["key"] != "blockingIssues"
                ],
            },
        }
        if include_metadata and not omit_metadata:
            result["metadata"] = schema
        if omitted_warnings:
            result["warningsOmitted"] = omitted_warnings
        return result

    def result_bytes(extra_pending: list[dict[str, Any]] | None = None) -> int:
        return len(dumps(result_data(extra_pending)).encode())

    def enqueue(source: str, direction: dict[str, Any], depth: int, start: int = 0) -> None:
        key = (direction["key"], source, start)
        if key not in expanded:
            expanded.add(key)
            queue.append(dict(source=source, direction=direction, depth=depth, start=start))

    if schema:
        for direction in directions:
            if direction["depth"]:
                enqueue(oid, direction, 1)
    else:
        reasons["metadata_unavailable"] = None
    if result_bytes() + 1024 > output_limit and include_metadata:
        omit_metadata = True
        reasons["maxResponseBytes"] = None
    if result_bytes() + 1024 > output_limit:
        raise AgilityError(
            "Detail root exceeds maxResponseBytes. Reduce selection or increase the configured limit."
        )
    while queue:
        queue.sort(key=lambda task: task["depth"])
        task = queue.pop(0)
        source, direction, depth, start = (
            task[key] for key in ("source", "direction", "depth", "start")
        )
        key = direction["key"] + "|" + source
        relation = direction["candidates"][0]
        try:
            async with asyncio.timeout_at(deadline):
                source_schema = await read_schema(source.split(":")[0])
                relation = next(
                    (
                        candidate
                        for candidate in direction["candidates"]
                        if source_schema["fields"].get(candidate, {}).get("type") == "Relation"
                    ),
                    relation,
                )
                result = await relation_page(
                    client,
                    read_schema,
                    oid=source,
                    relation=relation,
                    pageSize=page_size,
                    start=start,
                )
            existing_record = key in coverage
            record = coverage.setdefault(
                key,
                dict(
                    oid=source,
                    direction=direction["key"],
                    relation=relation,
                    fetched=0,
                    status="complete",
                    nextStart=None,
                ),
            )
            if not existing_record and result_bytes([task]) + 1024 > output_limit:
                del coverage[key]
                queue.insert(0, task)
                raise Limit("maxResponseBytes")
            if result["status"] != "ok":
                record.update(status=result["status"], reason=result["reason"])
                if result["status"] == "unavailable":
                    reasons["relationship_unavailable"] = None
                continue
            accepted = 0
            seen = page_seen.setdefault(key, set())
            for asset in result["assets"]:
                target = canonical_oid(asset.get("oid"))
                if not target:
                    reasons["invalid_related_oid"] = None
                    accepted += 1
                    continue
                if target in seen:
                    reasons["pagination_changed"] = None
                    accepted += 1
                    warnings.append(
                        dict(
                            code="pagination_changed",
                            oid=source,
                            relation=relation,
                            message="A related item repeated across pages; the collection may have changed during traversal.",
                        )
                    )
                    continue
                summary = item_summary(with_parent(asset))
                edge = {
                    "from": target if direction["reverse"] else source,
                    "to": source if direction["reverse"] else target,
                    "kind": direction["kind"],
                }
                edge_key = f"{edge['kind']}|{edge['from']}|{edge['to']}"
                reason = (
                    "maxNodes"
                    if target not in nodes and len(nodes) >= max_nodes
                    else "maxEdges"
                    if edge_key not in edges and len(edges) >= max_edges
                    else None
                )
                if reason:
                    reasons[reason] = None
                    record.update(status="partial", nextStart=start + accepted)
                    queue.insert(0, task | {"start": record["nextStart"]})
                    raise Limit(reason)
                new_node, new_edge = target not in nodes, edge_key not in edges
                if new_node:
                    nodes[target] = summary
                if new_edge:
                    edges[edge_key] = edge
                new_reach = target != oid and target not in reached[direction["key"]]
                if new_reach:
                    reached[direction["key"]][target] = depth
                record["fetched"] += 1
                if result_bytes([task | {"start": start + accepted}]) + 1024 > output_limit:
                    record["fetched"] -= 1
                    if new_reach:
                        del reached[direction["key"]][target]
                    if new_edge:
                        del edges[edge_key]
                    if new_node:
                        del nodes[target]
                    reasons["maxResponseBytes"] = None
                    record.update(status="partial", nextStart=start + accepted)
                    queue.insert(0, task | {"start": record["nextStart"]})
                    raise Limit("maxResponseBytes")
                seen.add(target)
                accepted += 1
                if summary.get("parentStatus") == "unavailable":
                    reasons["parent_unavailable"] = None
                if (
                    new_reach
                    and depth < direction["depth"]
                    and direction["key"] != "blockingIssues"
                ):
                    enqueue(target, direction, depth + 1)
            if "total" in record and "total" in result and record["total"] != result["total"]:
                reasons["pagination_changed"] = None
            if "total" in result:
                record["total"] = result["total"]
            else:
                record.pop("total", None)
            record.update(
                nextStart=result["nextStart"],
                status="complete" if result["nextStart"] is None else "partial",
            )
            if record["nextStart"] is not None:
                record["continuation"] = continuation(
                    source, relation, page_size, record["nextStart"]
                )
            if result.get("paginationStatus") == "stalled":
                reasons["pagination_stalled"] = None
                record.update(status="partial", reason=result["paginationWarning"])
            if result["nextStart"] is not None:
                if result["nextStart"] <= start or not result["assets"]:
                    reasons["pagination_stalled"] = None
                    record["status"] = "partial"
                else:
                    enqueue(source, direction, depth, result["nextStart"])
        except (Limit, TimeoutError) as error:
            reasons["maxDurationMs" if isinstance(error, TimeoutError) else error.reason] = None
            if not any(
                value["source"] == source and value["direction"]["key"] == direction["key"]
                for value in queue
            ):
                queue.insert(0, task)
            break
        except AgilityError as error:
            coverage[key] = dict(
                oid=source,
                direction=direction["key"],
                relation=relation,
                status="error",
                start=start,
                message=str(error),
            )
            warnings.append(
                dict(code="relationship_error", oid=source, relation=relation, message=str(error))
            )
            reasons["relationship_error"] = None
    for record in coverage.values():
        if record.get("nextStart") is not None:
            record["continuation"] = continuation(
                record["oid"], record["relation"], page_size, record["nextStart"]
            )
    if result_bytes() > output_limit and include_metadata:
        omit_metadata = True
        reasons["maxResponseBytes"] = None
    while warnings and result_bytes() > output_limit:
        warnings.pop()
        omitted_warnings += 1
        reasons["maxResponseBytes"] = None
    if result_bytes() > output_limit:
        raise AgilityError(
            "Detail root or traversal bookkeeping exceeds maxResponseBytes. Reduce selection or increase the configured limit."
        )
    return result_data()
