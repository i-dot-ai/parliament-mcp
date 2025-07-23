import pytest
from qdrant_client import AsyncQdrantClient

from parliament_mcp.models import DebateParent
from parliament_mcp.qdrant_data_loaders import QdrantHansardLoader
from parliament_mcp.settings import settings


@pytest.mark.asyncio
# @pytest.mark.integration
async def test_hansard_loader(qdrant_in_memory_test_client: AsyncQdrantClient):
    loader = QdrantHansardLoader(
        qdrant_client=qdrant_in_memory_test_client,
        collection_name=settings.HANSARD_CONTRIBUTIONS_COLLECTION,
        settings=settings,
    )
    await loader.load_all_contributions(from_date="2025-06-25", to_date="2025-06-25")

    count_result = await qdrant_in_memory_test_client.count(settings.HANSARD_CONTRIBUTIONS_COLLECTION)
    assert count_result.count >= 100


@pytest.mark.asyncio
async def test_get_debate_parents():
    loader = QdrantHansardLoader(
        qdrant_client=None, collection_name=settings.HANSARD_CONTRIBUTIONS_COLLECTION, settings=settings
    )
    debate_parents = await loader.get_debate_parents(
        date="2025-06-25", house="Commons", debate_ext_id="C6A6D738-6043-4D98-81C5-DC9AF7C646E9"
    )
    assert len(debate_parents) == 4

    assert debate_parents[0] == DebateParent(
        Id=4949262,
        Title="High-speed Internet",
        ParentId=4949261,
        ExternalId="C6A6D738-6043-4D98-81C5-DC9AF7C646E9",
    )
    assert debate_parents[1] == DebateParent(
        Id=4949261,
        Title="Science, Innovation and Technology",
        ParentId=4949260,
        ExternalId="1E65EA07-D2D1-4F38-AA00-6EF0E5E90DE7",
    )
    assert debate_parents[2] == DebateParent(
        Id=4949260,
        Title="Oral Answers to Questions",
        ParentId=4949258,
        ExternalId="39416357-1448-4834-9C24-8C4961253516",
    )
    assert debate_parents[3] == DebateParent(
        Id=4949258,
        Title="Commons Chamber",
        ParentId=None,
        ExternalId="3827400e-4d69-4c77-8be2-3d9fdad192b2",
    )
