"""Embedding client protocol and exception hierarchy.

Defines the EmbeddingClient Protocol (Strategy pattern) and custom exceptions
for embedding operations. Concrete implementations are added in subsequent tasks.
"""

import asyncio
import json
import logging
from itertools import batched
from typing import Protocol

import boto3
from botocore.exceptions import (
    ClientError,
    ConnectionClosedError,
    EndpointConnectionError,
    NoCredentialsError,
)
from tenacity import (
    RetryCallState,
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

from parliament_mcp.openai_helpers import (
    embed_batch as openai_embed_batch,
)
from parliament_mcp.openai_helpers import (
    embed_single as openai_embed_single,
)
from parliament_mcp.openai_helpers import (
    get_openai_client,
)
from parliament_mcp.settings import ParliamentMCPSettings

logger = logging.getLogger(__name__)

BEDROCK_BATCH_SIZE = 20  # Bedrock Titan models support up to 25 texts per request


class EmbeddingError(Exception):
    """Base exception for embedding operations."""


class EmbeddingAuthenticationError(EmbeddingError):
    """Raised when the embedding backend cannot authenticate."""


class EmbeddingModelError(EmbeddingError):
    """Raised when the configured model is not available."""


class EmbeddingAPIError(EmbeddingError):
    """Raised for non-retryable API errors."""


class EmbeddingClient(Protocol):
    """Protocol defining the embedding client interface.

    Any object with matching `embed_single` and `embed_batch` async methods
    satisfies this protocol via structural typing.
    """

    async def embed_single(self, text: str) -> list[float]:
        """Generate embedding for a single text string."""
        ...

    async def embed_batch(self, texts: list[str], batch_size: int = 100) -> list[list[float]]:
        """Generate embeddings for a list of texts."""
        ...


class AzureOpenAIEmbeddingClient:
    """Embedding client that delegates to Azure OpenAI via openai_helpers."""

    def __init__(self, settings: ParliamentMCPSettings):
        self._client = get_openai_client(settings)
        self._model = settings.AZURE_OPENAI_EMBEDDING_MODEL
        self._dimensions = settings.EMBEDDING_DIMENSIONS

    async def embed_single(self, text: str) -> list[float]:
        """Generate embedding for a single text string."""
        return await openai_embed_single(self._client, text, self._model, self._dimensions)

    async def embed_batch(self, texts: list[str], batch_size: int = 100) -> list[list[float]]:
        """Generate embeddings for a list of texts."""
        return await openai_embed_batch(self._client, texts, self._model, self._dimensions, batch_size)


def _is_retryable_bedrock_error(exception: BaseException) -> bool:
    """Return True for Bedrock errors that should be retried (throttling, service unavailable, connection errors)."""
    if isinstance(exception, EndpointConnectionError | ConnectionClosedError):
        return True
    if isinstance(exception, ClientError):
        error_code = exception.response.get("Error", {}).get("Code", "")
        return error_code in ("ThrottlingException", "ServiceUnavailableException")
    return False


def _log_retry_attempt(retry_state: RetryCallState) -> None:
    """Log retry attempts at WARNING level with attempt number and error detail."""
    attempt = retry_state.attempt_number
    error = retry_state.outcome.exception() if retry_state.outcome else None
    logger.warning("Bedrock retry attempt %d/3: %s", attempt, error)


class BedrockEmbeddingClient:
    """Embedding client that uses Amazon Bedrock for text embeddings."""

    def __init__(self, settings: ParliamentMCPSettings):
        self._client = boto3.client("bedrock-runtime", region_name=settings.AWS_REGION)
        self._model_id = settings.BEDROCK_EMBEDDING_MODEL_ID
        self._region = settings.AWS_REGION
        self._dimensions = settings.EMBEDDING_DIMENSIONS

    async def embed_single(self, text: str) -> list[float]:
        """Generate embedding for a single text string."""
        return (await self.embed_batch([text]))[0]

    async def embed_batch(self, texts: list[str], batch_size: int = BEDROCK_BATCH_SIZE) -> list[list[float]]:
        """Generate embeddings for a list of texts."""
        all_embeddings = []
        for batch in batched(texts, batch_size):
            batch_embeddings = await self._invoke_batch(list(batch))
            all_embeddings.extend(batch_embeddings)
        return all_embeddings

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception(_is_retryable_bedrock_error),
        before_sleep=_log_retry_attempt,
    )
    async def _invoke_single(self, text: str) -> list[float]:
        """Invoke Bedrock model for a single text."""
        body = json.dumps({"inputText": text, "dimensions": self._dimensions})

        try:
            response = await asyncio.to_thread(
                self._client.invoke_model,
                modelId=self._model_id,
                body=body,
                contentType="application/json",
                accept="application/json",
            )
        except NoCredentialsError as e:
            msg = f"Bedrock authentication failed: {e}. Verify AWS credentials are configured."
            raise EmbeddingAuthenticationError(msg) from e
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            error_message = e.response.get("Error", {}).get("Message", str(e))
            status_code = e.response.get("ResponseMetadata", {}).get("HTTPStatusCode", 0)

            if error_code == "AccessDeniedException":
                msg = f"Bedrock authentication failed: {error_message}. Verify AWS credentials are configured."
                raise EmbeddingAuthenticationError(msg) from e

            if error_code == "ResourceNotFoundException":
                msg = (
                    f"Bedrock model '{self._model_id}' not found in region '{self._region}'."
                    " Verify BEDROCK_EMBEDDING_MODEL_ID."
                )
                raise EmbeddingModelError(msg) from e

            if error_code == "ValidationException" and "model" in error_message.lower():
                msg = (
                    f"Bedrock model '{self._model_id}' not found in region '{self._region}'."
                    " Verify BEDROCK_EMBEDDING_MODEL_ID."
                )
                raise EmbeddingModelError(msg) from e

            if error_code in ("ThrottlingException", "ServiceUnavailableException"):
                raise

            msg = f"Bedrock API error (HTTP {status_code}): {error_message}"
            raise EmbeddingAPIError(msg) from e
        except (EndpointConnectionError, ConnectionClosedError):
            raise

        response_body = json.loads(response["body"].read())
        return response_body["embedding"]

    async def _invoke_batch(self, texts: list[str]) -> list[list[float]]:
        """Invoke Bedrock model for a batch of texts (one API call per text)."""
        return [await self._invoke_single(text) for text in texts]

def create_embedding_client(settings: ParliamentMCPSettings) -> EmbeddingClient:
    """Create an embedding client based on the configured backend.

    Returns BedrockEmbeddingClient when EMBEDDING_BACKEND is 'bedrock',
    AzureOpenAIEmbeddingClient otherwise.
    """
    if settings.EMBEDDING_BACKEND == "bedrock":
        return BedrockEmbeddingClient(settings)
    return AzureOpenAIEmbeddingClient(settings)
