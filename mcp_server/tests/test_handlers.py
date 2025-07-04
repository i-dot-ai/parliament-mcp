from datetime import UTC, datetime

import pytest
import pytest_asyncio
from elasticsearch import AsyncElasticsearch

from mcp_server.app.handlers import search_debates, search_hansard_contributions, search_parliamentary_questions
from parliament_mcp.elasticsearch_helpers import get_async_es_client
from parliament_mcp.settings import ParliamentMCPSettings


@pytest.fixture
def settings():
    return ParliamentMCPSettings()


@pytest_asyncio.fixture
async def es_client(settings: ParliamentMCPSettings):
    async with get_async_es_client(settings) as client:
        yield client


# mark async
@pytest.mark.asyncio
async def test_search_parliamentary_questions(settings: ParliamentMCPSettings, es_client: AsyncElasticsearch):
    results = await search_parliamentary_questions(
        es_client=es_client, index=settings.PARLIAMENTARY_QUESTIONS_INDEX, dateFrom="2025-06-20", dateTo="2025-06-25"
    )
    assert results is not None
    assert len(results) > 0

    results = await search_parliamentary_questions(
        es_client=es_client,
        index=settings.PARLIAMENTARY_QUESTIONS_INDEX,
        query="trains and railways",
    )
    assert results is not None
    assert len(results) > 0


@pytest.mark.asyncio
async def test_search_hansard_contributions(settings: ParliamentMCPSettings, es_client: AsyncElasticsearch):
    results = await search_hansard_contributions(
        es_client=es_client,
        index=settings.HANSARD_CONTRIBUTIONS_INDEX,
        query="trains and railways",
    )
    assert results is not None
    assert len(results) > 0


@pytest.mark.asyncio
async def test_search_hansard_contributions_with_member_id(
    settings: ParliamentMCPSettings, es_client: AsyncElasticsearch
):
    # Test with a memberId (Keir Starmer)
    results = await search_hansard_contributions(
        es_client=es_client,
        index=settings.HANSARD_CONTRIBUTIONS_INDEX,
        memberId=4514,
        maxResults=10,
    )
    assert results is not None
    assert len(results) > 0


@pytest.mark.asyncio
async def test_search_debates(settings: ParliamentMCPSettings, es_client: AsyncElasticsearch):
    results = await search_debates(
        es_client=es_client,
        index=settings.HANSARD_CONTRIBUTIONS_INDEX,
        date_to="2025-06-25",
    )
    assert results is not None
    assert len(results) > 0


@pytest.mark.asyncio
async def test_pmqs_are_on_wednesdays(settings: ParliamentMCPSettings, es_client: AsyncElasticsearch):
    results = await search_debates(
        es_client=es_client,
        index=settings.HANSARD_CONTRIBUTIONS_INDEX,
        # Not technically the title of the debate, but good to test with
        query="Prime Minister's Questions",
    )
    for result in results:
        # If 'Prime Minister' is in debate_parents.Title, then the debate is a PMQs
        is_pmqs = any("Prime Minister" in debate_parent["Title"] for debate_parent in result["debate_parents"])

        # PMQs are on Wednesdays
        if is_pmqs:
            date = datetime.fromisoformat(result["date"]).replace(tzinfo=UTC)
            assert date.weekday() == 2
