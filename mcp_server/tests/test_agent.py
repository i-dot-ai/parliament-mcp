import pytest
import pytest_asyncio
from agents import Agent, OpenAIResponsesModel, RunItem, Runner, RunResult, function_tool, set_tracing_disabled
from openai import AsyncAzureOpenAI

from mcp_server.app.api import get_government_posts, search_contributions, search_members
from parliament_mcp.settings import settings

set_tracing_disabled(True)


@pytest_asyncio.fixture
async def agent():
    """Agent fixture that uses test settings and running MCP server."""
    client = AsyncAzureOpenAI(
        api_key=settings.AZURE_OPENAI_API_KEY,
        azure_endpoint=settings.AZURE_OPENAI_ENDPOINT,
        api_version=settings.AZURE_OPENAI_API_VERSION,
    )
    agent = Agent(
        name="Parliament research assistant",
        model=OpenAIResponsesModel(openai_client=client, model="gpt-4o-mini"),
        tools=[
            function_tool(search_members),
            function_tool(search_contributions),
            function_tool(get_government_posts),
        ],
    )
    yield agent


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.filterwarnings("ignore::pydantic.json_schema.PydanticJsonSchemaWarning")
async def test_basic_agent(agent: Agent):
    """
    Test that the agent can answer a simple question.
    """
    result: RunResult = await Runner.run(agent, input="What is the capital of France?")
    assert "Paris" in result.final_output


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.filterwarnings("ignore::pydantic.json_schema.PydanticJsonSchemaWarning")
async def test_interaction_with_mcp_server(agent: Agent):
    """
    Test that the agent can use the MCP server to search the Hansard contributions index.
    """
    result: RunResult = await Runner.run(agent, input="Summarise some of the latest contributions by Keir Starmer.")
    tool_calls: list[RunItem] = list(filter(lambda item: item.type == "tool_call_item", result.new_items))
    assert any(tool_call.raw_item.name == "search_contributions" for tool_call in tool_calls)


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.filterwarnings("ignore::pydantic.json_schema.PydanticJsonSchemaWarning")
async def test_interaction_with_members_api(agent: Agent):
    """
    Test that the agent can use the MCP server to search the Members API.
    """
    result: RunResult = await Runner.run(agent, input="Who is the current Chancellor")
    tool_calls: list[RunItem] = list(filter(lambda item: item.type == "tool_call_item", result.new_items))
    assert any(tool_call.raw_item.name == "get_government_posts" for tool_call in tool_calls)
