from datetime import UTC, datetime

import pytest
from elasticsearch import AsyncElasticsearch

from parliament_mcp.mcp_server.handlers import (
    search_debates,
    search_hansard_contributions,
    search_parliamentary_questions,
)
from parliament_mcp.settings import settings


# mark async
@pytest.mark.asyncio
@pytest.mark.integration
async def test_search_parliamentary_questions(es_test_client: AsyncElasticsearch):
    """Test Parliamentary Questions search with test data."""
    results = await search_parliamentary_questions(
        es_client=es_test_client,
        index=settings.PARLIAMENTARY_QUESTIONS_INDEX,
        dateFrom="2025-06-20",
        dateTo="2025-06-25",
    )
    assert results is not None
    assert len(results) > 0

    results = await search_parliamentary_questions(
        es_client=es_test_client,
        index=settings.PARLIAMENTARY_QUESTIONS_INDEX,
        query="trains and railways",
    )
    assert results is not None
    # May be 0 if no matching data in test set
    assert len(results) > 0


@pytest.mark.asyncio
@pytest.mark.integration
async def test_search_hansard_contributions(es_test_client: AsyncElasticsearch):
    """Test Hansard contributions search with test data."""

    results = await search_hansard_contributions(
        es_client=es_test_client,
        index=settings.HANSARD_CONTRIBUTIONS_INDEX,
        query="debate",  # More generic query likely to match test data
    )
    assert results is not None
    assert len(results) > 0


@pytest.mark.asyncio
@pytest.mark.integration
async def test_search_hansard_contributions_with_member_id(es_test_client: AsyncElasticsearch):
    """Test Hansard contributions search with member ID."""

    # Test with a memberId (Deputy PM Angela Rayner stood in for PM in PMQs)
    results = await search_hansard_contributions(
        es_client=es_test_client,
        index=settings.HANSARD_CONTRIBUTIONS_INDEX,
        memberId=4356,
        maxResults=10,
    )
    assert results is not None
    assert len(results) > 0, "No results found"


@pytest.mark.asyncio
@pytest.mark.integration
async def test_search_debates(es_test_client: AsyncElasticsearch):
    """Test debates search with test data."""
    results = await search_debates(
        es_client=es_test_client,
        index=settings.HANSARD_CONTRIBUTIONS_INDEX,
        date_to="2025-06-25",
    )
    assert results is not None
    assert len(results) > 0


@pytest.mark.asyncio
@pytest.mark.integration
async def test_pmqs_are_on_wednesdays(es_test_client: AsyncElasticsearch):
    """Test that PMQs are on Wednesdays."""
    results = await search_debates(
        es_client=es_test_client,
        index=settings.HANSARD_CONTRIBUTIONS_INDEX,
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
