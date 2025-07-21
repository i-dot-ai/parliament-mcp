import logging
from typing import Any

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


async def generate_embeddings(
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

    for i in range(0, len(texts), batch_size):
        batch = texts[i : i + batch_size]

        try:
            response = await client.embeddings.create(
                input=batch,
                model=model,
                dimensions=dimensions,
            )

            batch_embeddings = [item.embedding for item in response.data]
            all_embeddings.extend(batch_embeddings)

            logger.info(
                "Generated embeddings for batch %d-%d of %d texts",
                i + 1,
                min(i + batch_size, len(texts)),
                len(texts),
            )
        except Exception:
            logger.exception("Error generating embeddings for batch %d", i // batch_size + 1)
            raise

    return all_embeddings


async def generate_single_embedding(
    client: AsyncAzureOpenAI,
    text: str,
    model: str,
    dimensions: int = 1024,
) -> list[float]:
    """Generate embedding for a single text.

    Args:
        client: AsyncAzureOpenAI client
        text: Text to embed
        model: Deployment name for the embedding model
        dimensions: Number of dimensions for the embeddings (default 1024)

    Returns:
        Embedding vector
    """
    embeddings = await generate_embeddings(client, [text], model, dimensions)
    return embeddings[0]


def chunk_text(
    text: str,
    chunk_size: int = 300,
    chunk_overlap: int = 50,
) -> list[str]:
    """Split text into overlapping chunks.

    Args:
        text: Text to chunk
        chunk_size: Maximum size of each chunk in words
        chunk_overlap: Number of words to overlap between chunks

    Returns:
        List of text chunks
    """
    words = text.split()
    chunks = []

    if len(words) <= chunk_size:
        return [text]

    step = chunk_size - chunk_overlap
    for i in range(0, len(words), step):
        chunk_words = words[i : i + chunk_size]
        chunk = " ".join(chunk_words)
        chunks.append(chunk)

        # If this was the last chunk and it's very small, merge with previous
        if i + chunk_size >= len(words) and len(chunk_words) < chunk_overlap and chunks and len(chunks) > 1:
            chunks[-2] = chunks[-2] + " " + chunks[-1]
            chunks.pop()

    return chunks


def prepare_document_for_embedding(
    document: dict[str, Any],
    text_fields: list[str],
    chunk_size: int = 300,
    chunk_overlap: int = 50,
) -> list[dict[str, Any]]:
    """Prepare a document for embedding by extracting and chunking text fields.

    Args:
        document: Document containing text fields
        text_fields: List of field names to extract text from
        chunk_size: Maximum size of each chunk in words
        chunk_overlap: Number of words to overlap between chunks

    Returns:
        List of prepared chunks with metadata
    """
    # Combine text from specified fields
    text_parts = []
    for field in text_fields:
        if document.get(field):
            text_parts.append(str(document[field]))

    if not text_parts:
        return []

    full_text = " ".join(text_parts)
    chunks = chunk_text(full_text, chunk_size, chunk_overlap)

    # Create a chunk document for each text chunk
    chunk_documents = []
    for i, chunk in enumerate(chunks):
        chunk_doc = {
            "text": chunk,
            "chunk_index": i,
            "total_chunks": len(chunks),
            **{k: v for k, v in document.items() if k not in text_fields},
        }
        chunk_documents.append(chunk_doc)

    return chunk_documents
