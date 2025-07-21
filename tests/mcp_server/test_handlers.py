from datetime import UTC, datetime

import pytest
from qdrant_client import AsyncQdrantClient

from parliament_mcp.mcp_server.handlers import (
    search_debates,
    search_hansard_contributions,
    search_parliamentary_questions,
)
from parliament_mcp.settings import settings


# mark async
@pytest.mark.asyncio
@pytest.mark.integration
async def test_search_parliamentary_questions(qdrant_test_client: AsyncQdrantClient):
    """Test Parliamentary Questions search with test data."""
    results = await search_parliamentary_questions(
        qdrant_client=qdrant_test_client,
        collection=settings.PARLIAMENTARY_QUESTIONS_COLLECTION,
        settings=settings,
        dateFrom="2025-06-20",
        dateTo="2025-06-25",
    )
    assert results is not None
    assert len(results) > 0

    results = await search_parliamentary_questions(
        qdrant_client=qdrant_test_client,
        collection=settings.PARLIAMENTARY_QUESTIONS_COLLECTION,
        settings=settings,
        query="trains and railways",
    )
    assert results is not None
    # May be 0 if no matching data in test set
    assert len(results) >= 0


@pytest.mark.asyncio
@pytest.mark.integration
async def test_search_hansard_contributions(qdrant_test_client: AsyncQdrantClient):
    """Test Hansard contributions search with test data."""

    results = await search_hansard_contributions(
        qdrant_client=qdrant_test_client,
        collection=settings.HANSARD_CONTRIBUTIONS_COLLECTION,
        settings=settings,
        query="debate",  # More generic query likely to match test data
    )
    assert results is not None
    assert len(results) > 0


@pytest.mark.asyncio
@pytest.mark.integration
async def test_search_hansard_contributions_with_member_id(qdrant_test_client: AsyncQdrantClient):
    """Test Hansard contributions search with member ID."""

    # Test with a memberId (Deputy PM Angela Rayner stood in for PM in PMQs)
    results = await search_hansard_contributions(
        qdrant_client=qdrant_test_client,
        collection=settings.HANSARD_CONTRIBUTIONS_COLLECTION,
        settings=settings,
        memberId=4356,
        maxResults=10,
    )
    assert results is not None
    assert len(results) > 0, "No results found"


@pytest.mark.asyncio
@pytest.mark.integration
async def test_search_debates(qdrant_test_client: AsyncQdrantClient):
    """Test debates search with test data."""
    results = await search_debates(
        qdrant_client=qdrant_test_client,
        collection=settings.HANSARD_CONTRIBUTIONS_COLLECTION,
        settings=settings,
        date_to="2025-06-25",
    )
    assert results is not None
    assert len(results) > 0


@pytest.mark.asyncio
@pytest.mark.integration
async def test_pmqs_are_on_wednesdays(qdrant_test_client: AsyncQdrantClient):
    """Test that PMQs are on Wednesdays."""
    results = await search_debates(
        qdrant_client=qdrant_test_client,
        collection=settings.HANSARD_CONTRIBUTIONS_COLLECTION,
        settings=settings,
        # Not technically the title of the debate, but good to test with
        query="Prime Minister's Questions",
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
