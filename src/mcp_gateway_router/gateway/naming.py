"""Names advertised to the client.

Tool names collide across servers — ``search`` exists on several. The client sees one
flat namespace, so the gateway qualifies every name with its server id. Server ids are
validated at config load to contain no ``__``, which makes a single split from the left
unambiguous even when the tool's own name contains one.
"""

from __future__ import annotations

from ..catalog import Tool
from .config import NAMESPACE_SEP


class AdvertisedNameError(ValueError):
    """An advertised tool name that does not resolve to (server_id, tool_name)."""


def advertised_name(tool: Tool) -> str:
    return f"{tool.server_id}{NAMESPACE_SEP}{tool.name}"


def parse_advertised(name: str) -> tuple[str, str]:
    server_id, separator, tool_name = name.partition(NAMESPACE_SEP)
    if not separator or not server_id or not tool_name:
        raise AdvertisedNameError(
            f"{name!r} is not a qualified tool name "
            f"(expected 'server_id{NAMESPACE_SEP}tool_name')"
        )
    return server_id, tool_name
