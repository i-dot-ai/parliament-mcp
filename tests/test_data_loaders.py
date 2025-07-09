import pytest

from parliament_mcp.data_loaders import ElasticHansardLoader
from parliament_mcp.models import DebateParent


@pytest.mark.asyncio
async def test_get_debate_parents():
    loader = ElasticHansardLoader(elastic_client=None, index_name="parliament_mcp_hansard_contributions")
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
