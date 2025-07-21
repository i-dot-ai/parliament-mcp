from typing import Literal

from qdrant_client import AsyncQdrantClient

from parliament_mcp.embedding_helpers import get_openai_client
from parliament_mcp.mcp_server.qdrant_handlers import (
    search_debates as qdrant_search_debates,
)
from parliament_mcp.mcp_server.qdrant_handlers import (
    search_hansard_contributions as qdrant_search_hansard_contributions,
)
from parliament_mcp.mcp_server.qdrant_handlers import (
    search_parliamentary_questions as qdrant_search_parliamentary_questions,
)
from parliament_mcp.settings import ParliamentMCPSettings


async def search_debates(
    *,
    qdrant_client: AsyncQdrantClient,
    collection: str,
    settings: ParliamentMCPSettings,
    query: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    house: str | None = None,
    max_results: int = 100,
) -> list[dict]:
    """
    Search debates for a given query, date range, and house using Qdrant.

    Either query or date range (or both) must be provided. House is optional.
    If only date_from is provided, returns debates from that date onwards.
    If only date_to is provided, returns debates up to and including that date.

    Returns a list of debate details (ID, title, date) ranked by relevancy.

    Args:
        qdrant_client: Qdrant client instance
        collection: Name of the Qdrant collection to search
        settings: Application settings
        query: Text to search for in debate titles (optional if date range is provided)
        date_from: Start date in format 'YYYY-MM-DD' (optional if query is provided)
        date_to: End date in format 'YYYY-MM-DD' (optional if query is provided)
        house: Filter by house (e.g., 'Commons', 'Lords'), optional
        max_results: Maximum number of results to return (default 100)

    Returns:
        List of debate details dictionaries

    Raises:
        ValueError: If neither query nor date range is provided
    """
    openai_client = get_openai_client(settings)

    return await qdrant_search_debates(
        qdrant_client=qdrant_client,
        openai_client=openai_client,
        settings=settings,
        collection=collection,
        query=query,
        date_from=date_from,
        date_to=date_to,
        house=house,
        max_results=max_results,
    )


async def search_hansard_contributions(
    *,
    qdrant_client: AsyncQdrantClient,
    collection: str,
    settings: ParliamentMCPSettings,
    query: str | None = None,
    memberId: int | None = None,
    dateFrom: str | None = None,
    dateTo: str | None = None,
    debateId: str | None = None,
    house: Literal["Commons", "Lords"] | None = None,
    maxResults: int = 100,
    min_score: float = 0.3,
) -> list[dict]:
    """
    Search Hansard contributions using Qdrant vector search.

    Args:
        qdrant_client: Qdrant client instance
        collection: Name of the Qdrant collection to search
        settings: Application settings
        query: Text to search for in contributions (optional)
        memberId: Member ID (optional)
        dateFrom: Start date in format 'YYYY-MM-DD' (optional)
        dateTo: End date in format 'YYYY-MM-DD' (optional)
        debateId: Debate ID (optional)
        house: House (Commons|Lords) (optional)
        maxResults: Maximum number of results to return (default 100)
        min_score: Minimum relevance score (default 0.5)

    Returns:
        List of Hansard contribution details dictionaries

    Raises:
        ValueError: If no search parameters are provided
    """
    openai_client = get_openai_client(settings)

    return await qdrant_search_hansard_contributions(
        qdrant_client=qdrant_client,
        openai_client=openai_client,
        settings=settings,
        collection=collection,
        query=query,
        memberId=memberId,
        dateFrom=dateFrom,
        dateTo=dateTo,
        debateId=debateId,
        house=house,
        maxResults=maxResults,
        min_score=min_score,
    )


async def search_parliamentary_questions(
    qdrant_client: AsyncQdrantClient,
    collection: str,
    settings: ParliamentMCPSettings,
    query: str | None = None,
    dateFrom: str | None = None,
    dateTo: str | None = None,
    party: str | None = None,
    member_name: str | None = None,
    member_id: int | None = None,
) -> list[dict]:
    """
    Search Parliamentary Questions using Qdrant vector search.

    Args:
        qdrant_client: Qdrant client instance
        collection: Name of the Qdrant collection to search
        settings: Application settings
        query: Text to search for in parliamentary questions
        dateFrom: Start date in format 'YYYY-MM-DD' (optional)
        dateTo: End date in format 'YYYY-MM-DD' (optional)
        party: Filter by party (optional)
        member_name: Filter by member name (optional)
        member_id: Filter by member id (optional)
    """
    openai_client = get_openai_client(settings)

    return await qdrant_search_parliamentary_questions(
        qdrant_client=qdrant_client,
        openai_client=openai_client,
        settings=settings,
        collection=collection,
        query=query,
        dateFrom=dateFrom,
        dateTo=dateTo,
        party=party,
        member_name=member_name,
        member_id=member_id,
    )
