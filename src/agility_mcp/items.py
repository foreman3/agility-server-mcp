"""Parent normalization, query enrichment and instance metadata."""

import re
from typing import Any

from .errors import AgilityError
from .xml import OID_PATTERN, assert_name

ITEM_TYPES = {"Epic", "Story", "Defect", "TestSet", "PrimaryWorkitem", "Workitem"}
PARENT_SELECTION = ["Super", "Super.Name", "Super.Number"]


def canonical_oid(oid: Any) -> str | None:
    return (
        ":".join(oid.split(":")[:2])
        if isinstance(oid, str) and re.fullmatch(OID_PATTERN, oid, re.ASCII)
        else None
    )


def unique(values: list[Any]) -> list[Any]:
    result: list[Any] = []
    for value in values:
        if value not in result:
            result.append(value)
    return result


def item_selection(asset_type: str, select: list[Any]) -> list[Any]:
    nested = any(isinstance(field, dict) and field.get("from") == "Super" for field in select)
    return unique(
        select
        + (
            [field for field in PARENT_SELECTION if field != "Super" or not nested]
            if asset_type in ITEM_TYPES
            else []
        )
    )


def first(value: Any) -> Any:
    return (
        value[0]
        if isinstance(value, list) and value
        else None
        if isinstance(value, list)
        else value
    )


def with_parent(item: dict[str, Any], asset_type: str | None = None) -> dict[str, Any]:
    asset_type = asset_type or item.get("oid", item.get("_oid", "")).split(":")[0]
    if asset_type not in ITEM_TYPES:
        return item
    attrs = item.get("attributes", item)
    relations = item.get("relations", {})
    ref = first(relations.get("Super", attrs.get("Super", attrs.get("Super.ID"))))
    oid = canonical_oid(
        ref
        if isinstance(ref, str)
        else next((ref[key] for key in ("oid", "_oid", "idref", "id") if key in ref), None)
        if isinstance(ref, dict)
        else None
    )
    embedded = ref if isinstance(ref, dict) else {}
    name = first(attrs.get("Super.Name"))
    if name is None:
        name = embedded.get("attributes", {}).get("Name", embedded.get("Name"))
    number = first(attrs.get("Super.Number"))
    if number is None:
        number = embedded.get("attributes", {}).get("Number", embedded.get("Number"))
    selected = "Super" in relations or any(
        key in attrs for key in ("Super", "Super.ID", "Super.Name", "Super.Number")
    )
    parent = (
        {"oid": oid, "id": oid, "name": name or None, "number": number or None, "relation": "Super"}
        if oid
        else None
    )
    status = (
        ("resolved" if parent and parent["name"] is not None else "unresolved")
        if oid
        else "none"
        if selected and ref is None and not name
        else "unavailable"
    )
    return item | {
        "parent": parent,
        "parentId": oid,
        "parentName": parent["name"] if parent else None,
        "parentNumber": parent["number"] if parent else None,
        "parentStatus": status,
    }


def prepare_queries(queries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = []
    for query in queries:
        select = [
            value if isinstance(value, str) else prepare_queries([value])[0]
            for value in query.get("select", ["Name"])
        ]
        asset_type = (
            "Workitem"
            if query.get("from")
            in ("Subs", "SubsAndDown", "Dependencies", "Dependants", "Workitems")
            else query.get("from", "")
        )
        result.append(query | {"select": item_selection(asset_type, select)})
    return result


def normalize_query_items(value: Any) -> Any:
    if isinstance(value, list):
        return [normalize_query_items(child) for child in value]
    if not isinstance(value, dict):
        return value
    result = {key: normalize_query_items(child) for key, child in value.items()}
    return with_parent(result) if value.get("_oid") else result


def array(value: Any) -> list[Any]:
    return [] if value is None else value if isinstance(value, list) else [value]


def describe_metadata(document: dict[str, Any], expected_type: str) -> dict[str, Any]:
    node = next(
        (
            value
            for value in array(document.get("AssetType", document.get("Meta", {}).get("AssetType")))
            if isinstance(value, dict) and value.get("@name") == expected_type
        ),
        None,
    )
    if node is None:
        raise AgilityError(f"Agility metadata did not describe {expected_type}.")
    fields = {}
    for value in array(node.get("AttributeDefinition")):
        name = assert_name(value["@name"])
        field = {
            "name": name,
            **({"type": value["@attributetype"]} if "@attributetype" in value else {}),
        }
        for key, attr in (
            ("required", "isrequired"),
            ("readOnly", "isreadonly"),
            ("multiValue", "ismultivalue"),
            ("custom", "iscustom"),
        ):
            field[key] = str(value.get("@" + attr, "")).lower() == "true"
        target = value.get("RelatedAsset", {}).get("@nameref")
        reciprocal = value.get("ReciprocalRelation", {}).get("@tokenref")
        if target:
            field["targetType"] = assert_name(target)
        if reciprocal and re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*\.[A-Za-z][A-Za-z0-9_]*", reciprocal):
            field["reciprocal"] = reciprocal.split(".")[1]
        fields[name] = field
    return {
        "assetType": expected_type,
        "baseType": node.get("Base", {}).get("@nameref"),
        "fields": fields,
        "operations": [
            value["@name"] for value in array(node.get("Operation")) if value.get("@name")
        ],
        "version": node.get("@version"),
    }


def item_summary(item: dict[str, Any]) -> dict[str, Any]:
    attrs = item.get("attributes", item)
    oid = canonical_oid(item.get("oid", item.get("_oid")))
    result = {
        "oid": oid,
        "assetType": oid.split(":")[0] if oid else None,
        "name": attrs.get("Name"),
        "number": attrs.get("Number"),
    }
    if item.get("parentStatus"):
        result |= {key: item[key] for key in ("parent", "parentId", "parentName", "parentStatus")}
    return result
