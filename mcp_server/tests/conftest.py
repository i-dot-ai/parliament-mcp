"""Conftest for MCP server tests - imports shared fixtures from root tests directory."""

from tests.conftest import (
    elasticsearch_container_url,
    es_test_client,
)

# Make fixtures available to this test module
__all__ = [
    "elasticsearch_container_url",
    "es_test_client",
]
