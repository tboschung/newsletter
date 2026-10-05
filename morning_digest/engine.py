from __future__ import annotations

import logging
from collections.abc import Callable

import httpx

from .collectors import collect_source
from .config import EngineConfig
from .models import Item


LOG = logging.getLogger(__name__)
SourceResult = Callable[[str, bool, str], None]


class ContentEngine:
    """Universal source runner; behavior is supplied entirely by an engine config."""

    def __init__(self, config: EngineConfig):
        self.config = config

    def collect(
        self,
        client: httpx.Client,
        result_callback: SourceResult | None = None,
        categories: frozenset[str] | None = None,
    ) -> tuple[list[Item], list[str]]:
        items: list[Item] = []
        failures: list[str] = []
        for source in self.config.sources:
            if categories is not None and source.category not in categories:
                continue
            try:
                items.extend(collect_source(client, source))
                if result_callback:
                    result_callback(source.name, True, "")
            except Exception as exc:
                LOG.warning("Source %s failed: %s", source.name, exc)
                failures.append(source.name)
                if result_callback:
                    result_callback(source.name, False, str(exc))
        return items, failures
