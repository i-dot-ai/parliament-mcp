import logging
from itertools import batched

import httpx
from openai import AsyncAzureOpenAI

from parliament_mcp.settings import ParliamentMCPSettings

logger = logging.getLogger(__name__)


def get_openai_client(settings: ParliamentMCPSettings) -> AsyncAzureOpenAI:
    """Get an async Azure OpenAI client."""
    return AsyncAzureOpenAI(
        api_key=settings.AZURE_OPENAI_API_KEY,
        api_version=settings.AZURE_OPENAI_API_VERSION,
        azure_endpoint=settings.AZURE_OPENAI_ENDPOINT,
        http_client=httpx.AsyncClient(timeout=30.0),
    )


async def embed_single(
    client: AsyncAzureOpenAI,
    text: str,
    model: str,
    dimensions: int = 1024,
) -> list[float]:
    """Generate a single embedding for a text using Azure OpenAI."""
    response = await client.embeddings.create(
        input=text,
        model=model,
        dimensions=dimensions,
    )
    return response.data[0].embedding


async def embed_batch(
    client: AsyncAzureOpenAI,
    texts: list[str],
    model: str,
    dimensions: int = 1024,
    batch_size: int = 100,
) -> list[list[float]]:
    """Generate embeddings for a list of texts using Azure OpenAI.

    Args:
        client: AsyncAzureOpenAI client
        texts: List of texts to embed
        model: Deployment name for the embedding model
        dimensions: Number of dimensions for the embeddings (default 1024)
        batch_size: Number of texts to process in each API call

    Returns:
        List of embedding vectors
    """
    all_embeddings = []

    for i, batch in enumerate(batched(texts, batch_size)):
        try:
            response = await client.embeddings.create(
                input=batch,
                model=model,
                dimensions=dimensions,
            )

            batch_embeddings = [item.embedding for item in response.data]
            all_embeddings.extend(batch_embeddings)

        except Exception:
            logger.exception("Error generating embeddings for batch %d", i // batch_size + 1)
            raise

    return all_embeddings
