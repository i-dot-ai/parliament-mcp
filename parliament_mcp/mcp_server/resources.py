"""Shared OpenAI + Qdrant resources used by both MCP tools and REST endpoints.

The MCP lifespan and the FastAPI lifespan both read from the same singleton so
that the QdrantQueryHandler is created once per app process and reused.
"""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any

from parliament_mcp.openai_helpers import get_openai_client
from parliament_mcp.qdrant_helpers import get_async_qdrant_client
from parliament_mcp.settings import settings

from .qdrant_query_handler import QdrantQueryHandler

_shared: dict[str, Any] = {}


@asynccontextmanager
async def initialize_shared_resources() -> AsyncGenerator[dict[str, Any]]:
    """Build the shared resources for the app lifetime."""
    openai_client = get_openai_client(settings)
    async with get_async_qdrant_client(settings) as qdrant_client:
        _shared["qdrant_query_handler"] = QdrantQueryHandler(qdrant_client, openai_client, settings)
        _shared["openai_client"] = openai_client
        try:
            yield _shared
        finally:
            _shared.clear()


def get_shared_resources() -> dict[str, Any]:
    """Return the shared-resources dict (empty before the lifespan runs)."""
    return _shared
