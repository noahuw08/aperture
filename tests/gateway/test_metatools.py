"""``find_tools`` — the search, its result text, and what it reports disclosing.

Pure tests: a hand-built ``Catalog``, no pool, no transport, no SDK, no subprocess.
That isolation is the reason this is a module rather than a branch inside
``Gateway.call_tool``.
"""

from mcp_gateway_router.catalog import Catalog, Tool
from mcp_gateway_router.gateway.config import GATEWAY_SERVER_ID
from mcp_gateway_router.gateway.metatools import FIND_TOOLS, SENTINEL, MetaTools


def _catalog():
    return Catalog(
        [
            Tool("github", "list_releases", "List the releases published in a repository",
                 {"type": "object", "properties": {"owner": {"type": "string"}}}),
            Tool("github", "get_me", "Details about the authenticated user", {"type": "object"}),
            Tool("notion", "API-post-search", "Search Notion pages by title", {"type": "object"}),
            Tool("playwright", "browser_close", "Close the browser page", {"type": "object"}),
        ]
    )


def test_disabled_advertises_nothing_and_handles_nothing():
    """Default-off must be indistinguishable from the meta-tool not existing."""
    meta = MetaTools(enabled=False)

    assert meta.advertise() == []
    assert meta.handles(GATEWAY_SERVER_ID) is False


def test_enabled_advertises_one_tool_in_the_gateway_namespace():
    meta = MetaTools(enabled=True)

    (tool,) = meta.advertise()

    assert tool.key == (GATEWAY_SERVER_ID, FIND_TOOLS)
    assert tool.input_schema["required"] == ["query"]
    assert "query" in tool.input_schema["properties"]
    assert meta.handles(GATEWAY_SERVER_ID) is True
    assert meta.handles("github") is False


def test_search_returns_matching_schemas_and_reports_them_disclosed():
    meta = MetaTools(enabled=True, k=2)

    text, disclosed = meta.call(FIND_TOOLS, {"query": "list releases"}, _catalog())

    assert ("github", "list_releases") in disclosed
    assert "github__list_releases" in text
    # The schema, not just the name — the model has to construct a call from this.
    assert '"owner"' in text


def test_the_result_carries_an_unbroken_sentinel():
    """One token, no separators. `GATEWAY-HINT-7F3A` came back as `GATEWAY-7F3A` in an
    earlier probe, so a substring match reported a hint the model had quoted in full as
    never seen."""
    meta = MetaTools(enabled=True)

    text, _ = meta.call(FIND_TOOLS, {"query": "releases"}, _catalog())

    assert SENTINEL in text
    assert "-" not in SENTINEL


def test_k_bounds_how_many_are_disclosed():
    meta = MetaTools(enabled=True, k=2)

    _, disclosed = meta.call(FIND_TOOLS, {"query": "search"}, _catalog())

    assert len(disclosed) == 2


def test_a_missing_query_explains_itself_and_discloses_nothing():
    meta = MetaTools(enabled=True)

    text, disclosed = meta.call(FIND_TOOLS, {}, _catalog())

    assert disclosed == set()
    assert "query" in text.lower()


def test_an_empty_catalog_says_so_rather_than_returning_nothing_silently():
    meta = MetaTools(enabled=True)

    text, disclosed = meta.call(FIND_TOOLS, {"query": "releases"}, Catalog([]))

    assert disclosed == set()
    assert "no tools" in text.lower()


def test_an_unknown_meta_tool_name_is_reported_not_raised():
    meta = MetaTools(enabled=True)

    text, disclosed = meta.call("nope", {"query": "x"}, _catalog())

    assert disclosed == set()
    assert "nope" in text


def test_a_raising_scorer_degrades_to_an_honest_message():
    """Never fabricate. Returning the first k tools on a broken search is worse than
    failing, because the agent cannot tell the difference."""

    def broken(catalog):
        raise RuntimeError("no model")

    meta = MetaTools(enabled=True, scorer_factory=broken)

    text, disclosed = meta.call(FIND_TOOLS, {"query": "releases"}, _catalog())

    assert disclosed == set()
    assert "could not" in text.lower()


def test_call_never_raises_whatever_the_arguments():
    meta = MetaTools(enabled=True)

    for arguments in ({}, {"query": None}, {"query": ""}, {"query": 7}, None, []):
        text, disclosed = meta.call(FIND_TOOLS, arguments, _catalog())
        assert isinstance(text, str)
        assert isinstance(disclosed, set)
