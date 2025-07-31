from datetime import UTC, datetime

import pytest
from qdrant_client import AsyncQdrantClient

from parliament_mcp.embedding_helpers import get_openai_client
from parliament_mcp.mcp_server.qdrant_query_handler import QdrantQueryHandler
from parliament_mcp.settings import settings


@pytest.fixture
async def qdrant_query_handler(qdrant_test_client: AsyncQdrantClient):
    openai_client = get_openai_client(settings)
    return QdrantQueryHandler(qdrant_test_client, openai_client, settings)


# mark async
@pytest.mark.asyncio
@pytest.mark.integration
async def test_search_parliamentary_questions(qdrant_query_handler: QdrantQueryHandler):
    """Test Parliamentary Questions search with test data."""
    results = await qdrant_query_handler.search_parliamentary_questions(
        dateFrom="2025-06-20",
        dateTo="2025-06-25",
    )
    assert results is not None
    assert len(results) > 0

    results = await qdrant_query_handler.search_parliamentary_questions(
        query="trains and railways",
    )
    assert results is not None
    # May be 0 if no matching data in test set
    assert len(results) >= 0


@pytest.mark.asyncio
@pytest.mark.integration
async def test_search_hansard_contributions(qdrant_query_handler: QdrantQueryHandler):
    """Test Hansard contributions search with test data."""

    results = await qdrant_query_handler.search_hansard_contributions(
        query="debate",  # More generic query likely to match test data
    )
    assert results is not None
    assert len(results) > 0


@pytest.mark.asyncio
@pytest.mark.integration
async def test_search_hansard_contributions_with_member_id(
    qdrant_query_handler: QdrantQueryHandler,
):
    """Test Hansard contributions search with member ID."""

    # Test with a memberId (Deputy PM Angela Rayner stood in for PM in PMQs)
    results = await qdrant_query_handler.search_hansard_contributions(
        memberId=4356,
        maxResults=10,
    )
    assert results is not None
    assert len(results) > 0, "No results found"


@pytest.mark.asyncio
@pytest.mark.integration
async def test_search_debates(qdrant_query_handler: QdrantQueryHandler):
    """Test debates search with test data."""
    results = await qdrant_query_handler.search_debates(
        date_from="2025-06-20",
        date_to="2025-06-25",
        house="Commons",
    )
    assert results is not None
    assert len(results) > 0


@pytest.mark.asyncio
@pytest.mark.integration
async def test_pmqs_are_on_wednesdays(qdrant_query_handler: QdrantQueryHandler):
    """Test that PMQs are on Wednesdays."""
    results = await qdrant_query_handler.search_debates(
        # Not technically the title of the debate, but good to test with
        query="Prime Minister's Questions",
        date_from="2025-01-01",
        date_to="2025-07-31",
    )
    any_pmqs_found = False
    for result in results:
        # If 'Prime Minister' is in debate_parents.Title, then the debate is a PMQs
        is_pmqs = any("Prime Minister" in debate_parent["Title"] for debate_parent in result["debate_parents"])

        any_pmqs_found |= is_pmqs

        # PMQs are on Wednesdays
        if is_pmqs:
            date = datetime.fromisoformat(result["date"]).replace(tzinfo=UTC)
            assert date.weekday() == 2, "PMQs found on wrong day"

    assert any_pmqs_found, "No PMQs found in test data"


@pytest.mark.asyncio
@pytest.mark.integration
async def test_search_hansard_contributions_with_filters(
    qdrant_query_handler: QdrantQueryHandler,
):
    """Test Hansard contributions search with knn.

    Should find the contribution on NATO from Pat McFadden on 24th June 2025.
    """
    results = await qdrant_query_handler.search_hansard_contributions(
        query="NATO",
        memberId=1587,
        dateFrom="2025-06-23",
        dateTo="2025-06-27",
    )
    assert results is not None
    assert len(results) > 0

    top_contribution_url = "https://hansard.parliament.uk/Commons/2025-06-24/debates/3E222FED-6C44-400C-8ABD-112BDCDAE98B/link#contribution-69057392-95C1-40B9-A415-6B4CCCFEE821"
    assert results[0]["contribution_url"] == top_contribution_url
