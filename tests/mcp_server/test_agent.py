import pytest
from agents import Agent, RunItem, Runner, RunResult, set_tracing_disabled

set_tracing_disabled(True)


@pytest.mark.asyncio
@pytest.mark.integration
async def test_basic_agent(test_mcp_agent: Agent):
    """
    Test that the agent can answer a simple question.
    """
    result: RunResult = await Runner.run(test_mcp_agent, input="What is the capital of France?")
    assert "Paris" in result.final_output


@pytest.mark.asyncio
@pytest.mark.integration
async def test_interaction_with_mcp_server(test_mcp_agent: Agent):
    """
    Test that the agent can use the MCP server to search the Hansard contributions index.
    """
    result: RunResult = await Runner.run(
        test_mcp_agent, input="Summarise some of the latest contributions by Keir Starmer."
    )
    tool_calls: list[RunItem] = list(filter(lambda item: item.type == "tool_call_item", result.new_items))
    assert any(tool_call.raw_item.name == "search_contributions" for tool_call in tool_calls)


@pytest.mark.asyncio
@pytest.mark.integration
async def test_interaction_with_members_api(test_mcp_agent: Agent):
    """
    Test that the agent can use the MCP server to search the Members API.
    """
    result: RunResult = await Runner.run(test_mcp_agent, input="Who is the current Chancellor")
    tool_calls: list[RunItem] = list(filter(lambda item: item.type == "tool_call_item", result.new_items))
    assert any(tool_call.raw_item.name == "get_government_posts" for tool_call in tool_calls)


@pytest.mark.asyncio
# @pytest.mark.integration
async def test_we_can_find_relevant_contributions(test_mcp_agent: Agent):
    """
    Test that the agent can use the MCP server to search the Members API.
    """
    result: RunResult = await Runner.run(
        test_mcp_agent,
        input="""
        Search for contributions on NATO between 23rd and 27th June 2025 by Pat McFadden.
        Provide the url of relevant contributions.""",
    )
    tool_calls: list[RunItem] = list(filter(lambda item: item.type == "tool_call_item", result.new_items))
    assert any(tool_call.raw_item.name == "search_contributions" for tool_call in tool_calls), (
        "No search_contributions tool call found"
    )

    assert "NATO" in result.final_output, "NATO not found in the final output"

    contribution_url = "https://hansard.parliament.uk/Commons/2025-06-24/debates/3E222FED-6C44-400C-8ABD-112BDCDAE98B/link#contribution-69057392-95C1-40B9-A415-6B4CCCFEE821"
    assert contribution_url in result.final_output, "Contribution URL not found in the final output"
