import pytest

from parliament_mcp.data_loaders import ElasticHansardLoader
from parliament_mcp.models import DebateParent


@pytest.mark.asyncio
async def test_get_debate_parents():
    loader = ElasticHansardLoader(elastic_client=None, index_name="parliament_mcp_hansard_contributions")
    debate_parents = await loader.get_debate_parents(date="2025-07-08", house="Lords", debate_section_id=4955651)
    assert len(debate_parents) == 2
    assert debate_parents[0] == DebateParent(
        Id=4955651,
        Title="Government Performance against Fiscal Rules",
        ParentId=4955641,
        ExternalId="1AFCC6ED-CC4E-473E-81A3-1B9A7502A89E",
    )
    assert debate_parents[1] == DebateParent(
        Id=4955641, Title="Lords Chamber", ParentId=None, ExternalId="639d454e-1704-4d26-a1a1-d44f3826b219"
    )
