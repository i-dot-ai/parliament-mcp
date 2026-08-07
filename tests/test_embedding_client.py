"""Tests for BedrockEmbeddingClient."""

import io
import json
from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError, NoCredentialsError
from tenacity import RetryError

from parliament_mcp.embedding_client import (
    BedrockEmbeddingClient,
    EmbeddingAPIError,
    EmbeddingAuthenticationError,
    EmbeddingModelError,
)
from parliament_mcp.settings import ParliamentMCPSettings


@pytest.fixture
def bedrock_settings():
    """Create settings configured for Bedrock backend."""
    return ParliamentMCPSettings(
        EMBEDDING_BACKEND="bedrock",
        BEDROCK_EMBEDDING_MODEL_ID="amazon.titan-embed-text-v2:0",
        _env_file=None,
    )


def _make_bedrock_response(body_dict: dict) -> dict:
    """Helper to create a mock Bedrock invoke_model response."""
    return {"body": io.BytesIO(json.dumps(body_dict).encode())}


def _make_client_error(code: str, message: str, status_code: int) -> ClientError:
    """Helper to create a botocore ClientError."""
    return ClientError(
        {
            "Error": {"Code": code, "Message": message},
            "ResponseMetadata": {"HTTPStatusCode": status_code},
        },
        "InvokeModel",
    )


class TestRequestBodyFormatting:
    """Test that BedrockEmbeddingClient formats request bodies correctly."""

    @patch("parliament_mcp.embedding_client.boto3.client")
    async def test_single_text_request_body(self, mock_boto3_client, bedrock_settings):
        mock_client = MagicMock()
        mock_client.invoke_model.return_value = _make_bedrock_response(
            {"embedding": [0.1, 0.2, 0.3], "inputTextTokenCount": 3}
        )
        mock_boto3_client.return_value = mock_client

        client = BedrockEmbeddingClient(bedrock_settings)
        await client.embed_single("hello world")

        call_kwargs = mock_client.invoke_model.call_args[1]
        body = json.loads(call_kwargs["body"])
        assert body["inputText"] == "hello world"
        assert "texts" not in body
        assert body["dimensions"] == bedrock_settings.EMBEDDING_DIMENSIONS
        assert call_kwargs["modelId"] == "amazon.titan-embed-text-v2:0"
        assert call_kwargs["contentType"] == "application/json"
        assert call_kwargs["accept"] == "application/json"

    @patch("parliament_mcp.embedding_client.boto3.client")
    async def test_batch_texts_request_body(self, mock_boto3_client, bedrock_settings):
        mock_client = MagicMock()

        def invoke_side_effect(**kwargs):
            return _make_bedrock_response({"embedding": [0.1, 0.2], "inputTextTokenCount": 2})

        mock_client.invoke_model.side_effect = invoke_side_effect
        mock_boto3_client.return_value = mock_client

        client = BedrockEmbeddingClient(bedrock_settings)
        await client.embed_batch(["text one", "text two"])

        # Each text gets its own API call with inputText format
        assert mock_client.invoke_model.call_count == 2
        first_call_kwargs = mock_client.invoke_model.call_args_list[0][1]
        first_body = json.loads(first_call_kwargs["body"])
        assert first_body["inputText"] == "text one"
        assert "texts" not in first_body
        assert first_body["dimensions"] == bedrock_settings.EMBEDDING_DIMENSIONS


class TestResponseParsing:
    """Test that single and batch embedding responses are parsed correctly."""

    @patch("parliament_mcp.embedding_client.boto3.client")
    async def test_single_embedding_response(self, mock_boto3_client, bedrock_settings):
        expected_embedding = [0.1, 0.2, 0.3, 0.4, 0.5]
        mock_client = MagicMock()
        mock_client.invoke_model.return_value = _make_bedrock_response(
            {"embedding": expected_embedding, "inputTextTokenCount": 5}
        )
        mock_boto3_client.return_value = mock_client

        client = BedrockEmbeddingClient(bedrock_settings)
        result = await client.embed_single("test text")

        assert result == expected_embedding

    @patch("parliament_mcp.embedding_client.boto3.client")
    async def test_batch_embedding_response(self, mock_boto3_client, bedrock_settings):
        expected_embeddings = [[0.1, 0.2, 0.3], [0.4, 0.5, 0.6], [0.7, 0.8, 0.9]]
        mock_client = MagicMock()
        # Each text gets a separate call returning a single embedding
        mock_client.invoke_model.side_effect = [
            _make_bedrock_response({"embedding": e, "inputTextTokenCount": 3})
            for e in expected_embeddings
        ]
        mock_boto3_client.return_value = mock_client

        client = BedrockEmbeddingClient(bedrock_settings)
        result = await client.embed_batch(["text1", "text2", "text3"])

        assert result == expected_embeddings
        assert mock_client.invoke_model.call_count == 3

    @patch("parliament_mcp.embedding_client.boto3.client")
    async def test_batch_splits_into_chunks(self, mock_boto3_client, bedrock_settings):
        """Test that large batches are split according to batch_size."""
        mock_client = MagicMock()
        # 3 texts with batch_size=2 means 2 batches, but each text is a separate API call
        mock_client.invoke_model.side_effect = [
            _make_bedrock_response({"embedding": [0.1], "inputTextTokenCount": 1}),
            _make_bedrock_response({"embedding": [0.2], "inputTextTokenCount": 1}),
            _make_bedrock_response({"embedding": [0.3], "inputTextTokenCount": 1}),
        ]
        mock_boto3_client.return_value = mock_client

        client = BedrockEmbeddingClient(bedrock_settings)
        result = await client.embed_batch(["a", "b", "c"], batch_size=2)

        assert result == [[0.1], [0.2], [0.3]]
        assert mock_client.invoke_model.call_count == 3


class TestRetryBehavior:
    """Test retry behavior on throttling and transient errors."""

    @patch("parliament_mcp.embedding_client.boto3.client")
    async def test_retries_on_throttling_then_succeeds(self, mock_boto3_client, bedrock_settings):
        """Test that throttling errors are retried and eventually succeed."""
        throttle_error = _make_client_error("ThrottlingException", "Rate exceeded", 429)
        mock_client = MagicMock()
        mock_client.invoke_model.side_effect = [
            throttle_error,
            _make_bedrock_response({"embedding": [1.0, 2.0], "inputTextTokenCount": 2}),
        ]
        mock_boto3_client.return_value = mock_client

        client = BedrockEmbeddingClient(bedrock_settings)
        result = await client.embed_single("retry test")

        assert result == [1.0, 2.0]
        assert mock_client.invoke_model.call_count == 2

    @patch("parliament_mcp.embedding_client.boto3.client")
    async def test_retries_on_service_unavailable(self, mock_boto3_client, bedrock_settings):
        """Test that ServiceUnavailableException triggers retry."""
        service_error = _make_client_error(
            "ServiceUnavailableException", "Service unavailable", 503
        )
        mock_client = MagicMock()
        mock_client.invoke_model.side_effect = [
            service_error,
            service_error,
            _make_bedrock_response({"embedding": [0.5], "inputTextTokenCount": 1}),
        ]
        mock_boto3_client.return_value = mock_client

        client = BedrockEmbeddingClient(bedrock_settings)
        result = await client.embed_single("retry twice")

        assert result == [0.5]
        assert mock_client.invoke_model.call_count == 3

    @patch("parliament_mcp.embedding_client.boto3.client")
    async def test_exhausts_retries_on_persistent_throttling(self, mock_boto3_client, bedrock_settings):
        """Test that persistent throttling exhausts all 3 retry attempts."""
        throttle_error = _make_client_error("ThrottlingException", "Rate exceeded", 429)
        mock_client = MagicMock()
        mock_client.invoke_model.side_effect = throttle_error
        mock_boto3_client.return_value = mock_client

        client = BedrockEmbeddingClient(bedrock_settings)

        with pytest.raises(RetryError):
            await client.embed_single("always throttled")

        assert mock_client.invoke_model.call_count == 3


class TestNonRetryableErrors:
    """Test that non-retryable errors raise correct exception types without retrying."""

    @patch("parliament_mcp.embedding_client.boto3.client")
    async def test_validation_exception_raises_embedding_api_error(
        self, mock_boto3_client, bedrock_settings
    ):
        """Test generic ValidationException raises EmbeddingAPIError."""
        error = _make_client_error("ValidationException", "Invalid input", 400)
        mock_client = MagicMock()
        mock_client.invoke_model.side_effect = error
        mock_boto3_client.return_value = mock_client

        client = BedrockEmbeddingClient(bedrock_settings)

        with pytest.raises(EmbeddingAPIError, match="Bedrock API error"):
            await client.embed_single("bad input")

        # Should not retry non-retryable errors
        assert mock_client.invoke_model.call_count == 1

    @patch("parliament_mcp.embedding_client.boto3.client")
    async def test_resource_not_found_raises_embedding_model_error(
        self, mock_boto3_client, bedrock_settings
    ):
        """Test ResourceNotFoundException raises EmbeddingModelError."""
        error = _make_client_error("ResourceNotFoundException", "Model not found", 404)
        mock_client = MagicMock()
        mock_client.invoke_model.side_effect = error
        mock_boto3_client.return_value = mock_client

        client = BedrockEmbeddingClient(bedrock_settings)

        with pytest.raises(EmbeddingModelError, match="not found"):
            await client.embed_single("model missing")

        assert mock_client.invoke_model.call_count == 1

    @patch("parliament_mcp.embedding_client.boto3.client")
    async def test_validation_exception_with_model_raises_model_error(
        self, mock_boto3_client, bedrock_settings
    ):
        """Test ValidationException mentioning model raises EmbeddingModelError."""
        error = _make_client_error(
            "ValidationException", "The model identifier is invalid", 400
        )
        mock_client = MagicMock()
        mock_client.invoke_model.side_effect = error
        mock_boto3_client.return_value = mock_client

        client = BedrockEmbeddingClient(bedrock_settings)

        with pytest.raises(EmbeddingModelError, match="not found"):
            await client.embed_single("invalid model ref")

        assert mock_client.invoke_model.call_count == 1

    @patch("parliament_mcp.embedding_client.boto3.client")
    async def test_unknown_client_error_raises_embedding_api_error(
        self, mock_boto3_client, bedrock_settings
    ):
        """Test unknown ClientError raises EmbeddingAPIError."""
        error = _make_client_error("InternalServerError", "Something went wrong", 500)
        mock_client = MagicMock()
        mock_client.invoke_model.side_effect = error
        mock_boto3_client.return_value = mock_client

        client = BedrockEmbeddingClient(bedrock_settings)

        with pytest.raises(EmbeddingAPIError, match="Bedrock API error"):
            await client.embed_single("server error")

        assert mock_client.invoke_model.call_count == 1


class TestAuthenticationErrorMapping:
    """Test authentication error mapping."""

    @patch("parliament_mcp.embedding_client.boto3.client")
    async def test_no_credentials_raises_authentication_error(
        self, mock_boto3_client, bedrock_settings
    ):
        """Test NoCredentialsError raises EmbeddingAuthenticationError."""
        mock_client = MagicMock()
        mock_client.invoke_model.side_effect = NoCredentialsError()
        mock_boto3_client.return_value = mock_client

        client = BedrockEmbeddingClient(bedrock_settings)

        with pytest.raises(EmbeddingAuthenticationError, match="authentication failed"):
            await client.embed_single("no creds")

        assert mock_client.invoke_model.call_count == 1

    @patch("parliament_mcp.embedding_client.boto3.client")
    async def test_access_denied_raises_authentication_error(
        self, mock_boto3_client, bedrock_settings
    ):
        """Test AccessDeniedException raises EmbeddingAuthenticationError."""
        error = _make_client_error(
            "AccessDeniedException", "User is not authorized", 403
        )
        mock_client = MagicMock()
        mock_client.invoke_model.side_effect = error
        mock_boto3_client.return_value = mock_client

        client = BedrockEmbeddingClient(bedrock_settings)

        with pytest.raises(EmbeddingAuthenticationError, match="authentication failed"):
            await client.embed_single("access denied")

        assert mock_client.invoke_model.call_count == 1
