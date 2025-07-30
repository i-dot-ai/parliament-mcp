"""Test configuration and fixtures for Parliament MCP tests."""

import asyncio
import contextlib
import logging
import os
import time
import warnings
from collections.abc import AsyncGenerator, Generator
from pathlib import Path

import docker
import dotenv
import httpx
import pytest
import pytest_asyncio
import uvicorn
from agents import Agent, OpenAIResponsesModel
from agents.mcp import MCPServerStreamableHttp, MCPServerStreamableHttpParams
from qdrant_client import AsyncQdrantClient
from testcontainers.qdrant import QdrantContainer

from parliament_mcp.embedding_helpers import get_openai_client
from parliament_mcp.qdrant_data_loaders import QdrantHansardLoader, QdrantParliamentaryQuestionLoader
from parliament_mcp.qdrant_helpers import collection_exists, initialize_qdrant_collections
from parliament_mcp.settings import settings

# Load environment variables from .env file
dotenv.load_dotenv(override=True)

logger = logging.getLogger(__name__)

# This is the host port that the container will be bound to.
QDRANT_CONTAINER_HOST_PORT = 6333


def ensure_docker_connection():
    """Ensure Docker is accessible, setting DOCKER_HOST if needed."""
    # Socket paths to try in order
    socket_paths = [
        None,  # Default (let Docker SDK decide)
        Path.home() / ".docker/run/docker.sock",  # Docker Desktop
        Path.home() / ".colima/docker.sock",  # Colima
        Path("/var/run/docker.sock"),  # Standard Linux
    ]

    for socket_path in socket_paths:
        if socket_path and socket_path.exists():
            os.environ["DOCKER_HOST"] = f"unix://{socket_path}"

        try:
            docker_client = docker.from_env()
            docker_client.ping()
            return
        except docker.errors.DockerException:
            logger.warning("Failed to connect to Docker at DOCKER_HOST='%s'", os.environ.get("DOCKER_HOST"))
            continue
        else:
            return

    msg = "Failed to connect to Docker. Please check that Docker is running and that the Docker socket is accessible."
    msg += "You can try setting the DOCKER_HOST environment variable to a valid socket path."
    msg += "For example, on macOS, you can try setting it to ~/.docker/run/docker.sock"
    msg += "or ~/.colima/docker.sock"
    msg += "or /var/run/docker.sock"
    msg += "or /run/docker.sock"
    msg += "or /var/run/docker.sock.1"
    raise RuntimeError(msg)


@pytest.fixture(scope="session")
def qdrant_container_url() -> Generator[str]:
    """Reusable Qdrant container with persistent data volume."""
    ensure_docker_connection()

    # Create persistent volume path
    volume_path = Path(__file__).parent / ".parliament-test-qdrant-data"
    volume_path.mkdir(exist_ok=True)

    # Configure container with persistent volume
    container = QdrantContainer("qdrant/qdrant:latest")
    container.with_bind_ports(6333, host=QDRANT_CONTAINER_HOST_PORT)
    container.with_volume_mapping(host=str(volume_path), container="/qdrant/storage", mode="rw")

    with container:
        # Wait for Qdrant to be ready by checking the health endpoint
        container_url = f"http://{container.get_container_host_ip()}:{QDRANT_CONTAINER_HOST_PORT}"

        # Simple retry loop with timeout
        max_attempts = 30
        for _attempt in range(max_attempts):
            try:
                response = httpx.get(f"{container_url}/healthz", timeout=2.0)
                if response.status_code == 200:
                    break
            except httpx.RequestError:
                logger.debug("Qdrant not ready yet, retrying...")
            time.sleep(1)
        else:
            msg = f"Qdrant container failed to become ready after {max_attempts} seconds"
            raise RuntimeError(msg)

        yield container_url


@pytest_asyncio.fixture(scope="session")
async def qdrant_test_client(
    qdrant_container_url: str,
) -> AsyncGenerator[AsyncQdrantClient]:
    """Qdrant client with test data loaded (only loads once per session)."""

    qdrant_client = AsyncQdrantClient(url=qdrant_container_url)
    try:
        # Check if data already exists
        if await collection_exists(qdrant_client, settings.HANSARD_CONTRIBUTIONS_COLLECTION):
            # Check if collections actually have data
            hansard_info = await qdrant_client.get_collection(settings.HANSARD_CONTRIBUTIONS_COLLECTION)
            pq_info = await qdrant_client.get_collection(settings.PARLIAMENTARY_QUESTIONS_COLLECTION)

            if hansard_info.points_count > 0 and pq_info.points_count > 0:
                yield qdrant_client
                return

        # pytest warning (shows with -W flag)
        warnings.warn(
            "First-time test setup: Loading Parliamentary data. This will take a few minutes. "
            "This only happens once - subsequent runs will be faster.",
            UserWarning,
            stacklevel=0,
        )

        # Initialize Qdrant collections
        await initialize_qdrant_collections(qdrant_client, settings)

        # Load minimal test data
        # Load Hansard - Monday to Wednesday
        hansard_loader = QdrantHansardLoader(
            qdrant_client=qdrant_client,
            collection_name=settings.HANSARD_CONTRIBUTIONS_COLLECTION,
            settings=settings,
        )
        await hansard_loader.load_all_contributions("2025-06-23", "2025-06-25")

        # Load Parliamentary Questions - Monday only
        pq_loader = QdrantParliamentaryQuestionLoader(
            qdrant_client=qdrant_client,
            collection_name=settings.PARLIAMENTARY_QUESTIONS_COLLECTION,
            settings=settings,
        )
        await pq_loader.load_questions_for_date_range("2025-06-23", "2025-06-23")

        yield qdrant_client
    finally:
        await qdrant_client.close()


@pytest_asyncio.fixture(scope="function")
async def qdrant_in_memory_test_client() -> AsyncGenerator[AsyncQdrantClient]:
    """Qdrant client with test data loaded (only loads once per session)."""
    qdrant_client = AsyncQdrantClient(":memory:")
    await initialize_qdrant_collections(qdrant_client, settings)
    yield qdrant_client
    await qdrant_client.close()


@pytest_asyncio.fixture(scope="session")
async def test_mcp_server(qdrant_test_client: AsyncQdrantClient) -> AsyncGenerator[MCPServerStreamableHttp]:
    """Start the MCP server backed by the test Elasticsearch client."""
    config = uvicorn.Config("parliament_mcp.mcp_server.main:create_app", host="127.0.0.1", port=8081, log_level="info")
    server = uvicorn.Server(config=config)

    # Create a task for the server
    server_task = asyncio.create_task(server.serve())

    try:
        # Wait for server to be ready
        logger.info("Waiting for server to start")
        await asyncio.sleep(1)

        async with MCPServerStreamableHttp(
            params=MCPServerStreamableHttpParams(
                url="http://127.0.0.1:8081/mcp",
            )
        ) as mcp_client:
            yield mcp_client
    finally:
        server.should_exit = True
        with contextlib.suppress(asyncio.CancelledError):
            await server_task


@pytest_asyncio.fixture(scope="function")
async def test_mcp_agent(test_mcp_server: MCPServerStreamableHttp):
    """Agent fixture that uses test settings and running MCP server."""
    client = get_openai_client(settings)
    agent = Agent(
        name="Parliament research assistant",
        model=OpenAIResponsesModel(openai_client=client, model="gpt-4o-mini"),
        mcp_servers=[test_mcp_server],
    )
    yield agent


@pytest_asyncio.fixture(scope="session")
async def qdrant_cloud_test_client() -> AsyncGenerator[AsyncQdrantClient]:
    """Qdrant client with test data loaded (only loads once per session)."""
    qdrant_client = AsyncQdrantClient(
        url=settings.QDRANT_URL,
        api_key=settings.QDRANT_API_KEY,
        timeout=30.0,
    )
    yield qdrant_client
    await qdrant_client.close()
