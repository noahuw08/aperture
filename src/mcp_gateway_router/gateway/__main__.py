"""Run the gateway as an MCP server over stdio.

Logging goes to stderr and never stdout — stdout *is* the MCP transport, and a
stray print corrupts the protocol stream.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

from .server import serve


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args()

    logging.basicConfig(
        stream=sys.stderr,
        level=getattr(logging, args.log_level.upper()),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    asyncio.run(serve(args.config))


if __name__ == "__main__":
    main()
