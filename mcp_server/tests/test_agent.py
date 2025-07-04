import pytest
import pytest_asyncio
from agents import Agent, OpenAIResponsesModel, RunItem, Runner, RunResult, set_tracing_disabled
from agents.mcp import MCPServerStreamableHttp, MCPServerStreamableHttpParams
from openai import AsyncAzureOpenAI

from parliament_mcp.settings import ParliamentMCPSettings

set_tracing_disabled(True)


@pytest.fixture(scope="session")
def settings() -> ParliamentMCPSettings:
    return ParliamentMCPSettings()


@pytest_asyncio.fixture
async def agent(settings: ParliamentMCPSettings) -> Agent:
    client = AsyncAzureOpenAI(
        api_key=settings.AZURE_OPENAI_API_KEY,
        azure_endpoint=settings.AZURE_OPENAI_ENDPOINT,
        api_version=settings.AZURE_OPENAI_API_VERSION,
    )
    async with MCPServerStreamableHttp(
        name="Parlex MCP Server",
        params=MCPServerStreamableHttpParams(
            url=f"http://localhost:{settings.MCP_PORT}{settings.MCP_ROOT_PATH}",
            timeout=30,
            headers={"Content-Type": "application/json"},
        ),
    ) as parlex_mcp_server:
        yield Agent(
            name="Parlex research assistant",
            model=OpenAIResponsesModel(openai_client=client, model="gpt-4o-mini"),
            mcp_servers=[parlex_mcp_server],
        )


@pytest.mark.asyncio
async def test_basic_agent(agent: Agent):
    """
    Test that the agent can answer a simple question.
    """
    result: RunResult = await Runner.run(agent, input="What is the capital of France?")
    assert "Paris" in result.final_output


@pytest.mark.asyncio
async def test_interaction_with_mcp_server(agent: Agent):
    """
    Test that the agent can use the MCP server to search the Hansard contributions index.
    """
    result: RunResult = await Runner.run(agent, input="Summarise some of the latest contributions by Keir Starmer.")
    tool_calls: list[RunItem] = list(filter(lambda item: item.type == "tool_call_item", result.new_items))
    assert any(tool_call.raw_item.name == "search_contributions" for tool_call in tool_calls)


@pytest.mark.asyncio
async def test_interaction_with_members_api(agent: Agent):
    """
    Test that the agent can use the MCP server to search the Members API.
    """
    result: RunResult = await Runner.run(agent, input="Who is the current Chancellor")
    tool_calls: list[RunItem] = list(filter(lambda item: item.type == "tool_call_item", result.new_items))
    assert any(tool_call.raw_item.name == "get_government_posts" for tool_call in tool_calls)
