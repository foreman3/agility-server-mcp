import json

import pytest
from conftest import FIXTURES

from agility_mcp.config import load_config, normalize_base_url, parse_args
from agility_mcp.errors import AgilityError
from agility_mcp.items import describe_metadata, normalize_query_items, prepare_queries, with_parent
from agility_mcp.xml import asset_xml, oid_path, parse_assets, parse_xml


@pytest.mark.parametrize("case", json.loads((FIXTURES / "xml-baseline.json").read_text()))
def test_xml_javascript_parity(case):
    assert parse_xml(case["text"]) == case["xml"]
    assert parse_assets(case["text"]) == case["assets"]


def test_config_defaults_and_stable_targets():
    a = load_config({"baseUrl": "https://EXAMPLE.test/Agility/"}, {})
    b = load_config({"baseUrl": "https://example.test/Agility"}, {})
    assert a.credentialTarget == b.credentialTarget
    assert not a.enableWrites and a.authMode == "token" and not a.allowOperations
    assert (
        a.credentialTarget
        != load_config({"baseUrl": "https://example.test/Other"}, {}).credentialTarget
    )


@pytest.mark.parametrize(
    "url",
    [
        "http://example.test",
        "https://user:secret@example.test",
        "https://example.test/?token=x",
        "https://example.test/#x",
        "file:///tmp",
        "https://example.test/a%2fb",
    ],
)
def test_unsafe_url(url):
    with pytest.raises(AgilityError):
        normalize_base_url(url)


def test_config_precedence_validation_and_cli(tmp_path):
    options, positional = parse_args(
        [
            "serve",
            "--base-url=https://example.test/v1",
            "--enable-writes",
            "--allow-operations",
            "Close,Delete",
        ]
    )
    config = load_config(
        options, {"AGILITY_BASE_URL": "https://elsewhere.test", "AGILITY_ENABLE_WRITES": "false"}
    )
    assert positional == ["serve"] and config.baseUrl == "https://example.test/v1"
    assert config.allowOperations == ("Close", "Delete")
    for overrides in (
        {"retries": -1},
        {"maxConcurrency": 0},
        {"enableWrites": "yes"},
        {"allowOperations": "Delete"},
        {"restEndpoint": "../bad"},
    ):
        with pytest.raises(AgilityError):
            load_config({"baseUrl": "https://example.test", **overrides}, {})
    for args in (["--token", "secret"], ["--enable-writes=false"], ["--base-url"]):
        with pytest.raises(AgilityError):
            parse_args(args)
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"baseUrl": "https://file.test", "retries": 1}))
    assert load_config({"config": str(path)}, {"AGILITY_RETRIES": "3"}).retries == 3
    path.write_text('{"secret":"private"}')
    with pytest.raises(AgilityError, match="Unknown configuration keys"):
        load_config({"config": str(path)}, {})


def test_xml_values_collections_relations():
    result = parse_assets(
        '<Assets total="2"><Asset id="Story:1:4" href="/v1/Data/Story/1/4"><Attribute name="Name">A &amp; B</Attribute><Attribute name="Estimate">3</Attribute><Relation name="Owners"><Asset idref="Member:20"/></Relation></Asset><Asset id="Story:2"><Attribute name="Name">Second</Attribute></Asset></Assets>'
    )
    assert result["total"] == 2
    assert result["assets"][0]["attributes"] == {"Name": "A & B", "Estimate": "3"}
    assert result["assets"][0]["relations"]["Owners"][0]["oid"] == "Member:20"
    assert parse_assets('<Assets total="0"/>') == {"assets": [], "total": 0}
    assert parse_assets("<Assets/>") == {"assets": []}
    assert parse_assets(
        '<Asset id="Story:1"><Attribute name="Tag"><Value>A</Value><Value>B</Value></Attribute></Asset>'
    )["assets"][0]["attributes"]["Tag"] == ["A", "B"]


def test_xml_mutation_escaping_and_actions():
    xml = asset_xml(
        {"Name": '<hello> & "quotes"', "Estimate": 0, "Description": None},
        [
            dict(name="Scope", action="set", targets=["Scope:0"]),
            dict(name="Owners", action="add", targets=["Member:20"]),
            dict(name="Owner", action="set", targets=[]),
        ],
    )
    assert "&lt;hello&gt; &amp; &quot;quotes&quot;" in xml
    assert '<Relation name="Owners"><Asset idref="Member:20" act="add"/></Relation>' in xml
    assert '<Relation name="Owner" act="set"></Relation>' in xml
    parse_xml(xml)


def test_xml_injection_cardinality_and_dtd():
    for attributes in ({'Name" bad="x': "value"}, {"Name": "\x01"}, {"Name": {}}, {}):
        with pytest.raises(AgilityError):
            asset_xml(attributes)
    with pytest.raises(AgilityError):
        asset_xml({}, [dict(name="Scope", action="set", targets=["Scope:1", "Scope:2"])])
    for oid in ("../Story:1", "Story:1?op=Delete", "Story:1/../../Member:2"):
        with pytest.raises(AgilityError):
            oid_path(oid)
    with pytest.raises(AgilityError):
        oid_path("Story:1:2", False)
    for xml in (
        '<!DOCTYPE x [<!ENTITY x "secret">]><Asset>&x;</Asset>',
        "<Asset>",
        "<!DOCTYPE Asset><Asset/>",
    ):
        with pytest.raises(AgilityError):
            parse_xml(xml)


def test_parent_projections_embedded_references():
    result = parse_assets(
        '<Asset id="Story:10"><Attribute name="Super.Name">Portfolio parent</Attribute><Attribute name="Super.Number">E-00001</Attribute><Relation name="Super"><Asset idref="Epic:1:7"><Attribute name="Name">Embedded name</Attribute></Asset></Relation></Asset>'
    )
    item = with_parent(result["assets"][0])
    assert (item["parentId"], item["parentName"], item["parentNumber"], item["parentStatus"]) == (
        "Epic:1",
        "Portfolio parent",
        "E-00001",
        "resolved",
    )
    assert item["relations"]["Super"][0]["attributes"]["Name"] == "Embedded name"


def test_unknown_empty_and_unresolved_parents():
    for relations, status in (
        ({"Super": []}, "none"),
        ({"Super": [{"oid": "Epic:3"}]}, "unresolved"),
        ({}, "unavailable"),
        ({"Super": [{"oid": "invalid"}]}, "unavailable"),
        ({"Parent": [{"oid": "Theme:9"}]}, "unavailable"),
    ):
        assert (
            with_parent(dict(oid="Story:1", attributes={}, relations=relations))["parentStatus"]
            == status
        )


def test_nested_query_parents():
    query = prepare_queries(
        [
            {
                "from": "Epic",
                "select": [
                    "Name",
                    {"from": "Subs", "select": ["Name"]},
                    {"from": "Owners", "select": ["Name"]},
                ],
            }
        ]
    )[0]
    assert "Super" in query["select"]
    assert "Super.Name" in query["select"][1]["select"]
    assert query["select"][2]["select"] == ["Name"]
    result = normalize_query_items(
        [
            [
                {
                    "_oid": "Epic:1",
                    "Super": {"_oid": "Epic:2"},
                    "Super.Name": "Root",
                    "Subs": [
                        {
                            "_oid": "Story:10",
                            "Super.ID": [{"_oid": "Epic:1"}],
                            "Super.Name": "Feature",
                        }
                    ],
                }
            ]
        ]
    )
    assert result[0][0]["parentId"] == "Epic:2"
    assert result[0][0]["Subs"][0]["parentName"] == "Feature"


def test_singleton_metadata_definitions():
    schema = describe_metadata(
        parse_xml(
            '<AssetType name="Story"><Base nameref="PrimaryWorkitem"/><AttributeDefinition name="Dependencies" attributetype="Relation" ismultivalue="True" isrequired="False"><RelatedAsset nameref="PrimaryWorkitem"/><ReciprocalRelation tokenref="PrimaryWorkitem.Dependants"/></AttributeDefinition><Operation name="Close"/></AssetType>'
        ),
        "Story",
    )
    assert schema["fields"]["Dependencies"]["targetType"] == "PrimaryWorkitem"
    assert schema["fields"]["Dependencies"]["reciprocal"] == "Dependants"
    assert schema["fields"]["Dependencies"]["multiValue"]
    assert schema["operations"] == ["Close"]
