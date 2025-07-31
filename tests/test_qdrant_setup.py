"""Test that Qdrant test container setup works correctly."""

import pytest
from qdrant_client import AsyncQdrantClient

from parliament_mcp.qdrant_helpers import collection_exists
from parliament_mcp.settings import settings


@pytest.mark.asyncio
@pytest.mark.integration
async def test_qdrant_collections_exist(qdrant_test_client: AsyncQdrantClient):
    """Test that the required collections exist with data."""

    # Check if Parliamentary Questions collection exists
    result = await collection_exists(qdrant_test_client, settings.PARLIAMENTARY_QUESTIONS_COLLECTION)
    assert result

    # Check if Hansard Contributions collection exists
    assert await collection_exists(qdrant_test_client, settings.HANSARD_CONTRIBUTIONS_COLLECTION)

    # Check that collections have some data
    pq_info = await qdrant_test_client.get_collection(settings.PARLIAMENTARY_QUESTIONS_COLLECTION)
    hansard_info = await qdrant_test_client.get_collection(settings.HANSARD_CONTRIBUTIONS_COLLECTION)

    # At least some data should be loaded
    assert pq_info.points_count > 0
    assert hansard_info.points_count > 0


@pytest.mark.asyncio
@pytest.mark.integration
async def test_qdrant_some_data_loaded(qdrant_test_client: AsyncQdrantClient):
    """Test that basic scroll functionality works."""
    # Simple scroll across Parliamentary Questions collection
    result, _ = await qdrant_test_client.scroll(
        collection_name=settings.PARLIAMENTARY_QUESTIONS_COLLECTION,
        limit=5,
        with_payload=True,
    )

    assert len(result) > 0

    # Test Hansard collection as well
    hansard_result, _ = await qdrant_test_client.scroll(
        collection_name=settings.HANSARD_CONTRIBUTIONS_COLLECTION,
        limit=5,
        with_payload=True,
    )

    assert len(hansard_result) > 0
