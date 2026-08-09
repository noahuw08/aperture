"""Selection, shadow mode, and fail-open.

A thin adapter. It owns no ranking logic — it builds a ``DecisionContext``, hands it to
the existing ``Selector`` protocol, and records what happened.

**Fail-open is the point of this module.** The gateway sits in the critical path of
every session; if the scorer raises, is cold, or hangs, the client still receives a
working tool set. A proxy that can take a client down is unsellable at any ranking
quality, and this is cheaper to build now than to retrofit.
"""

from __future__ import annotations

import logging
from dataclasses import asdict

from ..catalog import Catalog, Tool
from ..selector import DecisionContext, Selector
from ..tokens import TokenCounter
from .config import GatewayConfig
from .log import ExposedTool, ExposureLog

logger = logging.getLogger(__name__)


def _context_payload(context: DecisionContext) -> dict:
    payload = asdict(context)
    # tools_called is a tuple of tuples; JSON-friendly and cheap to read back.
    payload["tools_called"] = [list(k) for k in context.tools_called]
    payload["scopes"] = list(context.scopes)
    return payload


class Policy:
    def __init__(
        self,
        config: GatewayConfig,
        selector: Selector,
        counter: TokenCounter,
        log: ExposureLog,
    ) -> None:
        self._config = config
        self._selector = selector
        self._counter = counter
        self._log = log

    def _safe_cost(self, tool: Tool) -> int | None:
        """Token cost for the log, or ``None`` if it could not be measured.

        This runs *after* selection has already succeeded or failed open, so an
        exception here would take down ``tools/list`` from outside the fail-open
        boundary — the counter can raise on a cold cache, an unreachable API, or an
        unfunded account. A missing cost degrades the log record; it never degrades
        the client's tool set. ``None`` rather than a placeholder number, so an
        unmeasured cost can never be mistaken for a measured one downstream.
        """
        try:
            return self._counter.cost(tool)
        except Exception:
            logger.warning("could not measure token cost for %s", tool.uid)
            return None

    def pinned_tools(self, catalog: Catalog) -> list[Tool]:
        """Pinned entries that are actually present. A stale pin is not fatal."""
        found = []
        for server_id, name in self._config.pinned:
            tool = catalog.get(server_id, name)
            if tool is None:
                logger.warning("pinned tool %s/%s is not in the catalog", server_id, name)
                continue
            found.append(tool)
        return found

    def decide(
        self,
        catalog: Catalog,
        catalog_hash: str,
        context: DecisionContext,
    ) -> list[Tool]:
        pinned = self.pinned_tools(catalog)
        version = self._selector.name

        try:
            chosen = self._selector.select(
                context, catalog, self._config.budget_tokens, self._counter
            )
        except Exception:
            logger.exception("selector %s failed; failing open to pinned core", version)
            chosen = pinned
            version = "fail-open"

        self._log.decision(
            session_id=context.session_id or "unknown",
            arm=self._config.arm,
            decision_point="C" if context.task else "A",
            catalog_hash=catalog_hash,
            selector_version=version,
            budget_tokens=self._config.budget_tokens,
            context=_context_payload(context),
            n_candidates=len(catalog),
            exposed=[
                ExposedTool(
                    tool_uid=tool.uid,
                    score=0.0,
                    propensity=1.0,
                    token_cost=self._safe_cost(tool),
                )
                for tool in chosen
            ],
        )

        if self._config.mode == "shadow":
            return list(catalog)
        return chosen
