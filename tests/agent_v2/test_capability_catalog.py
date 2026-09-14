from app.trading_agent.capability_catalog import build_catalog


def test_catalog_is_derived_from_visible_tools_and_describes_unknown_coverage():
    catalog = build_catalog({
        "tools": [
            {"name": "query_positions", "inputSchema": {"type": "object"}},
            {"name": "search_public", "inputSchema": {"type": "object"}},
        ],
        "modules": {
            "trading": {"connection_status": "connected", "agent_access": True},
        },
    })
    tools = {item["id"]: item for item in catalog["tools"]}
    assert set(tools) == {"query_positions", "search_public"}
    assert tools["query_positions"]["source_kind"] == "internal"
    assert tools["query_positions"]["coverage_status"] == "unknown"
    assert tools["search_public"]["source_kind"] == "public"
    assert catalog["version"]
    assert "public_research" in catalog["conditional_sources"]


def test_catalog_does_not_grant_tools_not_present_in_visible_input():
    catalog = build_catalog({"tools": [], "modules": {}})
    assert catalog["tools"] == []
