import asyncio
import logging
import os
from itertools import batched

import httpx
import openai
from aiolimiter import AsyncLimiter
from openai import AsyncAzureOpenAI
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_random_exponential,
)

from parliament_mcp.settings import ParliamentMCPSettings

logger = logging.getLogger(__name__)


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


# Throttles for Azure OpenAI embedding calls. Raise the env vars when on
# a larger quota. TPM, not RPM, is usually the binding constraint.
_EMBED_RATE_PER_SECOND = _env_float("OPENAI_EMBED_RATE_PER_SECOND", 1.5)
_EMBED_MAX_CONCURRENCY = _env_int("OPENAI_EMBED_MAX_CONCURRENCY", 2)
_EMBED_DEFAULT_BATCH_SIZE = _env_int("OPENAI_EMBED_BATCH_SIZE", 32)

_embed_limiter = AsyncLimiter(max_rate=_EMBED_RATE_PER_SECOND, time_period=1.0)
_embed_semaphore = asyncio.Semaphore(_EMBED_MAX_CONCURRENCY)


def get_openai_client(settings: ParliamentMCPSettings) -> AsyncAzureOpenAI:
    """Get an async Azure OpenAI client."""
    return AsyncAzureOpenAI(
        api_key=settings.AZURE_OPENAI_API_KEY,
        api_version=settings.AZURE_OPENAI_API_VERSION,
        azure_endpoint=settings.AZURE_OPENAI_ENDPOINT,
        # The SDK honours Retry-After on 429s; let it ride out short windows
        # before tenacity takes over for longer waits.
        max_retries=6,
        http_client=httpx.AsyncClient(timeout=60.0),
    )


async def embed_single(
    client: AsyncAzureOpenAI,
    text: str,
    model: str,
    dimensions: int = 1024,
) -> list[float]:
    """Generate a single embedding for a text using Azure OpenAI."""
    async with _embed_semaphore, _embed_limiter:
        response = await client.embeddings.create(
            input=text,
            model=model,
            dimensions=dimensions,
        )
    return response.data[0].embedding


@retry(
    retry=retry_if_exception_type((openai.RateLimitError, openai.APITimeoutError, openai.APIConnectionError)),
    wait=wait_random_exponential(min=2, max=60),
    stop=stop_after_attempt(8),
    reraise=True,
)
async def embed_batch(
    client: AsyncAzureOpenAI,
    texts: list[str],
    model: str,
    dimensions: int = 1024,
    batch_size: int = _EMBED_DEFAULT_BATCH_SIZE,
) -> list[list[float]]:
    """Generate embeddings for a list of texts using Azure OpenAI.

    Args:
        client: AsyncAzureOpenAI client
        texts: List of texts to embed
        model: Deployment name for the embedding model
        dimensions: Number of dimensions for the embeddings (default 1024)
        batch_size: Number of texts per API call (default tuned for S0 tier)

    Returns:
        List of embedding vectors
    """
    all_embeddings = []

    for i, batch in enumerate(batched(texts, batch_size)):
        try:
            async with _embed_semaphore, _embed_limiter:
                response = await client.embeddings.create(
                    input=batch,
                    model=model,
                    dimensions=dimensions,
                )

            batch_embeddings = [item.embedding for item in response.data]
            all_embeddings.extend(batch_embeddings)

        except openai.RateLimitError:
            # Let tenacity retry the whole batch with backoff.
            logger.warning("Azure OpenAI rate limit hit on batch %d; backing off", i // batch_size + 1)
            raise
        except Exception:
            logger.exception("Error generating embeddings for batch %d", i // batch_size + 1)
            raise

    return all_embeddings
