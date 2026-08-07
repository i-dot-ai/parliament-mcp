"""Azure OpenAI regression tests.

Ensures the Azure OpenAI embedding backend continues to work correctly
after the introduction of the Bedrock backend.
"""

from unittest.mock import AsyncMock, MagicMock, patch

from qdrant_client import AsyncQdrantClient

from parliament_mcp.embedding_client import (
    AzureOpenAIEmbeddingClient,
    create_embedding_client,
)
from parliament_mcp.mcp_server.qdrant_query_handler import QdrantQueryHandler
from parliament_mcp.settings import ParliamentMCPSettings


class TestSettingsAzureOpenAI:
    """Test settings configuration for Azure OpenAI backend."""

    def test_settings_loads_azure_openai_fields(self):
        """Settings should load Azure OpenAI fields when EMBEDDING_BACKEND=azure_openai."""
        settings = ParliamentMCPSettings(EMBEDDING_BACKEND="azure_openai", _env_file=None)
        assert settings.EMBEDDING_BACKEND == "azure_openai"
        assert settings.EMBEDDING_DIMENSIONS == 1024

    def test_default_backend_is_azure_openai(self, monkeypatch):
        """Default backend should be azure_openai."""
        monkeypatch.delenv("EMBEDDING_BACKEND", raising=False)
        monkeypatch.delenv("BEDROCK_EMBEDDING_MODEL_ID", raising=False)
        settings = ParliamentMCPSettings(_env_file=None)
        assert settings.EMBEDDING_BACKEND == "azure_openai"

    def test_bedrock_fields_not_required_when_azure_openai(self, monkeypatch):
        """Bedrock fields should not be required when backend is azure_openai."""
        monkeypatch.delenv("BEDROCK_EMBEDDING_MODEL_ID", raising=False)
        settings = ParliamentMCPSettings(EMBEDDING_BACKEND="azure_openai", _env_file=None)
        assert settings.BEDROCK_EMBEDDING_MODEL_ID is None


class TestAzureOpenAIDelegation:
    """Test that EmbeddingClient delegates to AsyncAzureOpenAI when configured for Azure."""

    @patch("parliament_mcp.embedding_client.get_openai_client")
    def test_factory_returns_azure_client(self, mock_get_client):
        """create_embedding_client should return AzureOpenAIEmbeddingClient for azure_openai backend."""
        mock_get_client.return_value = MagicMock()
        settings = ParliamentMCPSettings(EMBEDDING_BACKEND="azure_openai", _env_file=None)
        client = create_embedding_client(settings)
        assert isinstance(client, AzureOpenAIEmbeddingClient)
        mock_get_client.assert_called_once_with(settings)


class TestAzureOpenAIEmbeddings:
    """Test embedding operations with mocked Azure OpenAI."""

    @patch("parliament_mcp.embedding_client.openai_embed_single")
    @patch("parliament_mcp.embedding_client.get_openai_client")
    async def test_single_embedding_returns_correct_dimensions(self, mock_get_client, mock_embed_single):
        """Single embedding should return a vector of EMBEDDING_DIMENSIONS length."""
        mock_get_client.return_value = MagicMock()
        mock_embed_single.return_value = [0.1] * 1024
        settings = ParliamentMCPSettings(EMBEDDING_BACKEND="azure_openai", _env_file=None)
        client = AzureOpenAIEmbeddingClient(settings)
        result = await client.embed_single("test query")
        assert len(result) == 1024
        mock_embed_single.assert_called_once()

    @patch("parliament_mcp.embedding_client.openai_embed_batch")
    @patch("parliament_mcp.embedding_client.get_openai_client")
    async def test_batch_embedding_returns_correct_number_of_vectors(self, mock_get_client, mock_embed_batch):
        """Batch embedding should return the correct number of vectors."""
        mock_get_client.return_value = MagicMock()
        texts = ["text one", "text two", "text three"]
        mock_embed_batch.return_value = [[0.1] * 1024 for _ in texts]
        settings = ParliamentMCPSettings(EMBEDDING_BACKEND="azure_openai", _env_file=None)
        client = AzureOpenAIEmbeddingClient(settings)
        result = await client.embed_batch(texts)
        assert len(result) == 3
        assert all(len(v) == 1024 for v in result)
        mock_embed_batch.assert_called_once()


class TestQdrantQueryHandlerWithAzure:
    """Test QdrantQueryHandler produces valid results with Azure backend."""

    async def test_qdrant_query_handler_calls_embed_single(self):
        """QdrantQueryHandler should call embedding_client.embed_single for dense queries."""
        mock_embedding_client = AsyncMock()
        mock_embedding_client.embed_single.return_value = [0.1] * 1024

        settings = ParliamentMCPSettings(EMBEDDING_BACKEND="azure_openai", _env_file=None)
        qdrant_client = AsyncQdrantClient(":memory:")

        handler = QdrantQueryHandler(qdrant_client, mock_embedding_client, settings)
        result = await handler.embed_query_dense("test query")

        assert len(result) == 1024
        mock_embedding_client.embed_single.assert_called_once_with("test query")
        await qdrant_client.close()


class TestNoBedrockCalls:
    """Test that no Bedrock API calls are made when backend is azure_openai."""

    @patch("parliament_mcp.embedding_client.boto3.client")
    @patch("parliament_mcp.embedding_client.openai_embed_single")
    @patch("parliament_mcp.embedding_client.get_openai_client")
    async def test_no_bedrock_calls_when_azure(self, mock_get_client, mock_embed_single, mock_boto3_client):
        """No boto3 Bedrock client should be created when using Azure OpenAI backend."""
        mock_get_client.return_value = MagicMock()
        mock_embed_single.return_value = [0.1] * 1024
        settings = ParliamentMCPSettings(EMBEDDING_BACKEND="azure_openai", _env_file=None)
        client = AzureOpenAIEmbeddingClient(settings)
        await client.embed_single("test")
        mock_boto3_client.assert_not_called()
