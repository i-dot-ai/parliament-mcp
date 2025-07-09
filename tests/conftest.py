"""Test configuration and fixtures for Parliament MCP tests."""

import logging
import os
import warnings
from collections.abc import AsyncGenerator
from pathlib import Path

import docker
import pytest
import pytest_asyncio
from elasticsearch import AsyncElasticsearch, Elasticsearch
from testcontainers.core.waiting_utils import wait_for
from testcontainers.elasticsearch import ElasticSearchContainer

from parliament_mcp.data_loaders import load_data
from parliament_mcp.elasticsearch_helpers import initialize_elasticsearch_indices
from parliament_mcp.settings import settings

logger = logging.getLogger(__name__)

# This is the host port that the container will be bound to.
ES_CONTAINER_HOST_PORT = 9200


def ensure_docker_connection():
    """Ensure Docker is accessible, trying common socket locations if needed."""
    # Socket paths to try in order
    socket_paths = [
        None,  # Default (let Docker SDK decide)
        Path.home() / ".docker/run/docker.sock",  # Docker Desktop
        Path.home() / ".colima/docker.sock",  # Colima
        Path("/var/run/docker.sock"),  # Standard Linux
    ]

    for socket_path in socket_paths:
        if socket_path:
            if not socket_path.exists():
                continue
            os.environ["DOCKER_HOST"] = f"unix://{socket_path}"
        else:
            os.environ.pop("DOCKER_HOST", None)

        try:
            docker.from_env().ping()
        except docker.errors.DockerException:
            logger.warning("Failed to connect to Docker at %s", socket_path)
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
async def elasticsearch_container_url() -> AsyncGenerator[str]:
    """Reusable Elasticsearch container with persistent data volume."""
    ensure_docker_connection()

    # Create persistent volume path
    volume_path = Path(__file__).parent / ".parliament-test-es-data"
    volume_path.mkdir(exist_ok=True)

    # Configure container with persistent volume
    container = ElasticSearchContainer("docker.elastic.co/elasticsearch/elasticsearch:8.17.3")
    container.with_env("discovery.type", "single-node")
    container.with_env("xpack.security.enabled", "false")
    container.with_env("ES_JAVA_OPTS", "-Xms512m -Xmx512m")
    container.with_bind_ports(9200, host=ES_CONTAINER_HOST_PORT)

    # create a temporary sync client for the health check
    es_client = Elasticsearch(f"http://{container.get_container_host_ip()}:{ES_CONTAINER_HOST_PORT}")

    container.with_volume_mapping(host=str(volume_path), container="/usr/share/elasticsearch/data", mode="rw")
    with container:
        wait_for(lambda: es_client.cluster.health(wait_for_status="green"))
        yield f"http://{container.get_container_host_ip()}:{ES_CONTAINER_HOST_PORT}"


@pytest_asyncio.fixture(scope="function")
async def es_test_client(
    elasticsearch_container_url: str,
) -> AsyncGenerator[AsyncElasticsearch]:
    """Elasticsearch client with test data loaded (only loads once per session)."""

    async with AsyncElasticsearch(elasticsearch_container_url, node_class="httpxasync") as es_client:
        # Check if data already exists
        if await es_client.indices.exists(index=settings.HANSARD_CONTRIBUTIONS_INDEX):
            yield es_client
            return

        # pytest warning (shows with -W flag)
        warnings.warn(
            "First-time test setup: Loading Parliamentary data. This will take a few minutes. "
            "This only happens once - subsequent runs will be faster.",
            UserWarning,
            stacklevel=0,
        )

        # Initialize Elasticsearch indices and inference endpoints using the shared abstraction
        # This will try to use semantic_text fields first, but fall back to regular text fields
        # in test environments if the inference endpoint cannot be created
        await initialize_elasticsearch_indices(es_client, settings)

        # Load minimal test data
        # Load Hansard - Monday to Wednesday
        await load_data(es_client, settings, "hansard", "2025-06-23", "2025-06-25")

        # Load Parliamentary Questions - Monday only
        await load_data(es_client, settings, "parliamentary-questions", "2025-06-23", "2025-06-23")

        yield es_client
