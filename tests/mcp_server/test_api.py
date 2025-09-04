import json

import pytest
from agents.mcp import MCPServerStreamableHttp


@pytest.mark.asyncio
async def test_get_detailed_member_information(test_mcp_client: MCPServerStreamableHttp):
    result = await test_mcp_client.call_tool(
        tool_name="get_detailed_member_information",
        arguments={
            # Sir Iain Duncan Smith
            "member_id": 152,
            "include_committee_membership": True,
        },
    )

    result = json.loads(result.content[0].text)

    assert result is not None

    committee_membership = result["committee_membership"]
    assert isinstance(committee_membership, list)
    assert len(committee_membership) >= 4


@pytest.mark.asyncio
async def test_list_all_committees(test_mcp_client: MCPServerStreamableHttp):
    result = await test_mcp_client.call_tool(
        tool_name="list_all_committees",
        arguments={},
    )
    result = json.loads(result.content[0].text)
    assert result is not None
    assert len(result) > 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("committee_id", "expected"),
    [
        # Legislative scrutiny focused committee
        (760, {"name": "City of London (Markets) Bill"}),
        # Commons select committee
        (327, {"name": "Public Administration and Constitutional Affairs Committee"}),
    ],
)
async def test_get_committee_details(test_mcp_client: MCPServerStreamableHttp, committee_id: int, expected: dict):
    result = await test_mcp_client.call_tool(
        tool_name="get_committee_details",
        arguments={
            "committee_id": committee_id,
        },
    )
    result = json.loads(result.content[0].text)
    assert result is not None
    assert result["basic_committee_info"]["name"] == expected["name"]
