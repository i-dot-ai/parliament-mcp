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


@pytest.mark.asyncio
@pytest.mark.integration
async def test_consistent_output_between_local_and_cloud_data(
    es_test_client: AsyncElasticsearch, es_cloud_client: AsyncElasticsearch
):
    """
    Test that the local and cloud data are consistent.
    Searches for "NATO" between 23rd and 27th June 2025 for Pat McFadden's contribution on NATO.

    Should find this contribution https://hansard.parliament.uk/Commons/2025-06-24/debates/3E222FED-6C44-400C-8ABD-112BDCDAE98B/link#contribution-69057392-95C1-40B9-A415-6B4CCCFEE821

    """
    query = {
        "query": {
            "bool": {
                "filter": [
                    {"term": {"MemberId": 1587}},
                    {"range": {"SittingDate": {"gte": "2025-06-23"}}},
                    {"range": {"SittingDate": {"lte": "2025-06-27"}}},
                ],
                "must": [
                    {"semantic": {"field": "ContributionTextFull", "query": "NATO"}},
                ],
            }
        },
        "size": 3,
    }

    """Test that the local data works."""
    result = await es_test_client.search(index=settings.HANSARD_CONTRIBUTIONS_INDEX, body=query)
    assert len(result["hits"]["hits"]) > 0, "No results found in local data"

    """Test that the cloud data works."""
    result = await es_cloud_client.search(index=settings.HANSARD_CONTRIBUTIONS_INDEX, body=query)
    assert len(result["hits"]["hits"]) > 0, "No results found in cloud data"
