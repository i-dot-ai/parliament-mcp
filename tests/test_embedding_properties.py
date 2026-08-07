"""Property-based tests for the embedding client.

Uses Hypothesis to validate universal correctness properties from the design document.
"""

import io
import json
from unittest.mock import MagicMock, patch

import pytest
from hypothesis import assume, given
from hypothesis import settings as hypothesis_settings
from hypothesis.strategies import floats, integers, lists, text
from pydantic import ValidationError

from parliament_mcp.embedding_client import (
    AzureOpenAIEmbeddingClient,
    BedrockEmbeddingClient,
)
from parliament_mcp.settings import ParliamentMCPSettings


class TestInvalidBackendRejection:
    """Property 1: Invalid backend values are rejected.

    **Validates: Requirements 1.3**
    """

    @given(backend=text(min_size=1))
    @hypothesis_settings(max_examples=100)
    def test_invalid_backend_rejected(self, backend):
        """For any string that is not 'azure_openai' or 'bedrock', constructing
        ParliamentMCPSettings with EMBEDDING_BACKEND set to that string SHALL
        raise a validation error.
        """
        assume(backend not in ("azure_openai", "bedrock"))
        with pytest.raises(ValidationError):
            ParliamentMCPSettings(EMBEDDING_BACKEND=backend, _env_file=None)


class TestEmbeddingDimensionInvariant:
    """Property 2: Embedding dimension invariant.

    **Validates: Requirements 3.1, 3.2, 5.3, 5.4**
    """

    @given(texts_list=lists(text(min_size=1, max_size=50), min_size=1, max_size=10))
    @hypothesis_settings(max_examples=100)
    @patch("parliament_mcp.embedding_client.openai_embed_batch")
    @patch("parliament_mcp.embedding_client.get_openai_client")
    async def test_embedding_dimension_invariant_azure(self, mock_get_client, mock_embed_batch, texts_list):
        """For any non-empty list of text strings and the Azure backend (with mocked
        underlying API), calling embed_batch SHALL return exactly N vectors (where N
        is the input list length), each of exactly EMBEDDING_DIMENSIONS length.
        """
        dimensions = 1024
        mock_get_client.return_value = MagicMock()
        mock_embed_batch.return_value = [[0.1] * dimensions for _ in texts_list]

        settings = ParliamentMCPSettings(EMBEDDING_BACKEND="azure_openai", _env_file=None)
        client = AzureOpenAIEmbeddingClient(settings)
        result = await client.embed_batch(texts_list)

        assert len(result) == len(texts_list)
        assert all(len(v) == dimensions for v in result)

    @given(texts_list=lists(text(min_size=1, max_size=50), min_size=1, max_size=10))
    @hypothesis_settings(max_examples=100)
    @patch("parliament_mcp.embedding_client.boto3.client")
    async def test_embedding_dimension_invariant_bedrock(self, mock_boto3_client, texts_list):
        """For any non-empty list of text strings and the Bedrock backend (with mocked
        underlying API), calling embed_batch SHALL return exactly N vectors (where N
        is the input list length), each of exactly EMBEDDING_DIMENSIONS length.
        """
        dimensions = 1024
        mock_client = MagicMock()

        def invoke_side_effect(**kwargs):
            response = {"embedding": [0.1] * dimensions, "inputTextTokenCount": 5}
            return {"body": io.BytesIO(json.dumps(response).encode())}

        mock_client.invoke_model.side_effect = invoke_side_effect
        mock_boto3_client.return_value = mock_client

        settings = ParliamentMCPSettings(
            EMBEDDING_BACKEND="bedrock",
            BEDROCK_EMBEDDING_MODEL_ID="amazon.titan-embed-text-v2:0",
            _env_file=None,
        )
        client = BedrockEmbeddingClient(settings)
        result = await client.embed_batch(texts_list)

        assert len(result) == len(texts_list)
        assert all(len(v) == dimensions for v in result)


class TestBedrockResponseParsingRoundTrip:
    """Property 3: Bedrock response parsing round-trip.

    **Validates: Requirements 4.2, 4.3**
    """

    @given(
        embedding=lists(
            floats(min_value=-1.0, max_value=1.0, allow_nan=False, allow_infinity=False),
            min_size=64,
            max_size=64,
        )
    )
    @hypothesis_settings(max_examples=100)
    @patch("parliament_mcp.embedding_client.boto3.client")
    async def test_bedrock_response_parsing_roundtrip(self, mock_boto3_client, embedding):
        """For any valid embedding vector of the configured dimensions, wrapping it
        in the Bedrock response JSON format and parsing it through the
        BedrockEmbeddingClient response parser SHALL yield the original vector unchanged.
        """
        mock_client = MagicMock()
        response_body = {"embedding": embedding, "inputTextTokenCount": 5}
        mock_client.invoke_model.return_value = {"body": io.BytesIO(json.dumps(response_body).encode())}
        mock_boto3_client.return_value = mock_client

        settings = ParliamentMCPSettings(
            EMBEDDING_BACKEND="bedrock",
            BEDROCK_EMBEDDING_MODEL_ID="amazon.titan-embed-text-v2:0",
            EMBEDDING_DIMENSIONS=64,
            _env_file=None,
        )
        client = BedrockEmbeddingClient(settings)
        result = await client.embed_single("test")

        assert result == embedding


class TestBatchPartitioningCorrectness:
    """Property 4: Batch partitioning correctness.

    **Validates: Requirements 4.4**
    """

    @given(
        n_texts=integers(min_value=1, max_value=100),
        batch_size=integers(min_value=1, max_value=25),
    )
    @hypothesis_settings(max_examples=100)
    @patch("parliament_mcp.embedding_client.boto3.client")
    async def test_batch_partitioning_correctness(self, mock_boto3_client, n_texts, batch_size):
        """For any list of N texts where N > 0, the BedrockEmbeddingClient.embed_batch
        method SHALL invoke the underlying API exactly N times (one per text),
        and the total number of embeddings returned SHALL equal N.
        """
        mock_client = MagicMock()

        def invoke_side_effect(**kwargs):
            response = {"embedding": [0.1] * 1024, "inputTextTokenCount": 5}
            return {"body": io.BytesIO(json.dumps(response).encode())}

        mock_client.invoke_model.side_effect = invoke_side_effect
        mock_boto3_client.return_value = mock_client

        settings = ParliamentMCPSettings(
            EMBEDDING_BACKEND="bedrock",
            BEDROCK_EMBEDDING_MODEL_ID="amazon.titan-embed-text-v2:0",
            _env_file=None,
        )
        client = BedrockEmbeddingClient(settings)
        texts = [f"text_{i}" for i in range(n_texts)]
        result = await client.embed_batch(texts, batch_size=batch_size)

        assert mock_client.invoke_model.call_count == n_texts
        assert len(result) == n_texts
