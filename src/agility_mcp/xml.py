"""String-preserving XML normalization and safe mutation serialization."""

import math
import re
from typing import Any
from xml.etree.ElementTree import Element, ParseError

from defusedxml.common import DefusedXmlException
from defusedxml.ElementTree import fromstring

from .errors import AgilityError

NAME_PATTERN = r"[A-Za-z][A-Za-z0-9_]*"
OID_PATTERN = NAME_PATTERN + r":\d+(?::\d+)?"


def assert_name(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(NAME_PATTERN, value):
        raise AgilityError("Invalid asset, attribute, relation, or operation name.")
    return value


def oid_path(oid: str, allow_moment: bool = True) -> str:
    if (
        not isinstance(oid, str)
        or not re.fullmatch(OID_PATTERN, oid, re.ASCII)
        or (not allow_moment and oid.count(":") != 1)
    ):
        raise AgilityError(
            "Expected an OID such as Story:1234; mutations require an OID without a moment."
        )
    return oid.replace(":", "/")


def escape_xml(value: Any) -> str:
    text = "" if value is None else str(value).lower() if isinstance(value, bool) else str(value)
    if re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff\ufffe\uffff]", text):
        raise AgilityError("Value contains invalid XML characters.")
    for char, escaped in (
        ("&", "&amp;"),
        ("<", "&lt;"),
        (">", "&gt;"),
        ('"', "&quot;"),
        ("'", "&apos;"),
    ):
        text = text.replace(char, escaped)
    return text


def asset_xml(
    attributes: dict[str, Any] | None = None, relations: list[dict[str, Any]] | None = None
) -> str:
    parts = []
    for name, value in (attributes or {}).items():
        assert_name(name)
        if value is not None and not isinstance(value, (str, int, float, bool)):
            raise AgilityError("Attributes must be scalar values or null.")
        if isinstance(value, float) and not math.isfinite(value):
            raise AgilityError("Attribute numbers must be finite.")
        parts.append(f'<Attribute name="{name}" act="set">{escape_xml(value)}</Attribute>')
    for relation in relations or []:
        name = assert_name(relation["name"])
        action, targets = relation["action"], relation["targets"]
        if action not in ("set", "add", "remove"):
            raise AgilityError("Invalid relationship action.")
        if not isinstance(targets, list) or (len(targets) > 1 if action == "set" else not targets):
            raise AgilityError("set accepts zero or one target; add/remove require targets.")
        members = []
        for oid in targets:
            oid_path(oid, False)
            act = "" if action == "set" else f' act="{action}"'
            members.append(f'<Asset idref="{oid}"{act}/>')
        act = ' act="set"' if action == "set" else ""
        parts.append(f'<Relation name="{name}"{act}>{"".join(members)}</Relation>')
    if not parts:
        raise AgilityError("Provide at least one attribute or relationship.")
    return "<Asset>" + "".join(parts) + "</Asset>"


def _node(element: Element) -> Any:
    result: dict[str, Any] = {"@" + k: v for k, v in element.attrib.items()}
    for child in element:
        value = _node(child)
        if child.tag in ("Asset", "Attribute", "Relation", "Value"):
            result.setdefault(child.tag, []).append(value)
        elif child.tag in result:
            if not isinstance(result[child.tag], list):
                result[child.tag] = [result[child.tag]]
            result[child.tag].append(value)
        else:
            result[child.tag] = value
    text = (element.text or "") + "".join(child.tail or "" for child in element)
    if not result:
        return text
    if text:
        result["#text"] = text
    return result


def parse_xml(text: str) -> dict[str, Any]:
    if re.search(r"<!DOCTYPE|<!ENTITY", text, re.IGNORECASE):
        raise AgilityError("DTD and entity declarations are not accepted.")
    try:
        root = fromstring(text, forbid_dtd=True, forbid_entities=True, forbid_external=True)
    except (ParseError, DefusedXmlException, ValueError, TypeError):
        raise AgilityError("Agility returned invalid XML.") from None
    value = _node(root)
    result = {
        root.tag: [value] if root.tag in ("Asset", "Attribute", "Relation", "Value") else value
    }
    declaration = re.match(r"\ufeff?<\?xml\s+(.*?)\?>", text, re.S)
    if declaration:
        result["?xml"] = {
            "@" + key: value
            for key, _, value in re.findall(
                r"""([A-Za-z_:][\w.:-]*)\s*=\s*(["'])(.*?)\2""", declaration[1]
            )
        }
    return result


def _asset(node: Any) -> dict[str, Any]:
    if not isinstance(node, dict):
        node = {}
    attributes = {}
    for item in node.get("Attribute", []):
        attributes[item["@name"]] = (
            [
                value.get("#text", "") if isinstance(value, dict) else value
                for value in item["Value"]
            ]
            if "Value" in item
            else item.get("#text", "")
        )
    relations = {}
    for item in node.get("Relation", []):
        refs = []
        for ref in item.get("Asset", []):
            normalized = {}
            if "@idref" in ref or "@id" in ref:
                normalized["oid"] = ref.get("@idref", ref.get("@id"))
            if "@href" in ref:
                normalized["href"] = ref["@href"]
            if ref.get("Attribute"):
                normalized["attributes"] = _asset(ref)["attributes"]
            refs.append(normalized)
        relations[item["@name"]] = refs
    result = {key: node["@" + key] for key in ("oid", "href") if "@" + key in node}
    if "@id" in node:
        result["oid"] = node["@id"]
    return result | {"attributes": attributes, "relations": relations}


def parse_assets(text: str) -> dict[str, Any]:
    doc = parse_xml(text)
    if "Assets" in doc:
        node = doc["Assets"]
        if not isinstance(node, dict):
            return {"assets": []}
        result: dict[str, Any] = {"assets": [_asset(asset) for asset in node.get("Asset", [])]}
        if "@total" in node:
            try:
                total = float(node["@total"])
                if math.isfinite(total):
                    result["total"] = int(total) if total.is_integer() else total
            except ValueError:
                pass
        return result
    if "Asset" in doc:
        return {"assets": [_asset(node) for node in doc["Asset"]]}
    raise AgilityError("Agility returned an unexpected XML document (expected Asset or Assets).")
