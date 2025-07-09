"""Test that Elasticsearch test container setup works correctly."""

import pytest
from elasticsearch import AsyncElasticsearch

from parliament_mcp.settings import settings


@pytest.mark.asyncio
@pytest.mark.integration
async def test_elasticsearch_indices_exist(es_test_client: AsyncElasticsearch):
    """Test that the required indices exist with data."""
    # Check if Parliamentary Questions index exists
    result = await es_test_client.indices.exists(index=settings.PARLIAMENTARY_QUESTIONS_INDEX)
    assert result

    # Check if Hansard Contributions index exists
    assert await es_test_client.indices.exists(index=settings.HANSARD_CONTRIBUTIONS_INDEX)

    # Check that indices have some data
    pq_count = await es_test_client.count(index=settings.PARLIAMENTARY_QUESTIONS_INDEX)
    hansard_count = await es_test_client.count(index=settings.HANSARD_CONTRIBUTIONS_INDEX)

    # At least some data should be loaded
    assert pq_count["count"] > 0
    assert hansard_count["count"] > 0


@pytest.mark.asyncio
@pytest.mark.integration
async def test_elasticsearch_some_data_loaded(es_test_client: AsyncElasticsearch):
    """Test that basic search functionality works."""
    # Simple search across all indices
    result = await es_test_client.search(
        index=f"{settings.PARLIAMENTARY_QUESTIONS_INDEX},{settings.HANSARD_CONTRIBUTIONS_INDEX}",
        body={"query": {"match_all": {}}, "size": 5},
    )

    assert result["hits"]["total"]["value"] > 0
    assert len(result["hits"]["hits"]) > 0
