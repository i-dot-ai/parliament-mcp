"""Benchmzzark tests for MCP server tools."""

import logging
import statistics
import time

import pytest
from agents.mcp import MCPServerStreamableHttp

logger = logging.getLogger(__name__)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_mcp_server_sequential_tool_benchmark(test_mcp_client: MCPServerStreamableHttp):
    """Benchmark test that calls 5-6 tools in sequence through the actual MCP server."""

    async def make_tool_calls():
        await test_mcp_client.call_tool(
            "search_members",
            {
                "Name": "Keir Starmer",
            },
        )

        await test_mcp_client.call_tool(
            "search_parliamentary_questions",
            {"query": "GP funding", "asking_member_id": 5239},
        )

        await test_mcp_client.call_tool(
            "search_contributions",
            {
                "memberId": 4356,
                "query": "NATO",
            },
        )

        await test_mcp_client.call_tool(
            "search_debate_titles",
            {
                "query": "NATO",
            },
        )

    # cache warmup
    await make_tool_calls()

    n = 10
    times = []
    for _ in range(n):
        start_time = time.perf_counter()
        await make_tool_calls()
        end_time = time.perf_counter()
        times.append(end_time - start_time)

    average_time = statistics.mean(times)
    std_dev = statistics.stdev(times)
    logger.info("Average time taken: %s seconds", average_time)
    logger.info("Standard deviation: %s seconds", std_dev)
    logger.info("Min time taken: %s seconds", min(times))
    logger.info("Max time taken: %s seconds", max(times))

    assert average_time < 1.0, "Average time to complete 5 tool calls should be less than 1 second"
