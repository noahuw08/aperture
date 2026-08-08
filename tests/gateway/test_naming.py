import pytest

from mcp_gateway_router.catalog import Tool
from mcp_gateway_router.gateway.naming import (
    AdvertisedNameError,
    advertised_name,
    parse_advertised,
)


def _tool(server_id="github", name="search_code"):
    return Tool(server_id=server_id, name=name, description="d", input_schema={})


def test_advertised_name_joins_server_and_tool():
    assert advertised_name(_tool()) == "github__search_code"


def test_round_trip():
    tool = _tool()
    assert parse_advertised(advertised_name(tool)) == ("github", "search_code")


def test_tool_name_may_itself_contain_the_separator():
    tool = _tool(name="get__user__profile")
    assert parse_advertised(advertised_name(tool)) == ("github", "get__user__profile")


def test_unqualified_name_is_rejected():
    with pytest.raises(AdvertisedNameError, match="search_code"):
        parse_advertised("search_code")


def test_empty_server_id_is_rejected():
    with pytest.raises(AdvertisedNameError):
        parse_advertised("__search_code")
