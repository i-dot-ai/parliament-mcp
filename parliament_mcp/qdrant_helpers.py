import contextlib
import logging
from collections.abc import AsyncGenerator
from typing import Any

from qdrant_client import AsyncQdrantClient, models

from parliament_mcp.settings import ParliamentMCPSettings

logger = logging.getLogger(__name__)


@contextlib.asynccontextmanager
async def get_async_qdrant_client(
    settings: ParliamentMCPSettings,
) -> AsyncGenerator[AsyncQdrantClient]:
    """Gets an async Qdrant client from environment variables.

    Supports both cloud (via API key) and local connections.
    """
    logger.info("Connecting to Qdrant at %s", settings.QDRANT_URL)
    client = AsyncQdrantClient(url=settings.QDRANT_URL, api_key=settings.QDRANT_API_KEY, timeout=30)

    try:
        yield client
    finally:
        await client.close()


async def collection_exists(client: AsyncQdrantClient, collection_name: str) -> bool:
    """Checks if a collection exists in Qdrant."""
    try:
        await client.get_collection(collection_name)
    except Exception:  # noqa: BLE001
        return False
    else:
        return True


async def create_collection_if_none(
    client: AsyncQdrantClient,
    collection_name: str,
    vector_size: int,
    distance: models.Distance = models.Distance.DOT,
) -> None:
    """Create Qdrant collection if it doesn't exist."""
    logger.info("Creating collection - %s", collection_name)

    if not await collection_exists(client, collection_name):
        await client.create_collection(
            collection_name=collection_name,
            vectors_config={
                "text_dense": models.VectorParams(size=vector_size, distance=distance, on_disk=True),
            },
            sparse_vectors_config={
                "text_sparse": models.SparseVectorParams(
                    index=models.SparseIndexParams(
                        on_disk=True,
                    ),
                    modifier=models.Modifier.IDF,
                )
            },
        )
        logger.info("Created collection - %s", collection_name)
    else:
        logger.info("Collection already exists - %s", collection_name)


async def delete_collection_if_exists(client: AsyncQdrantClient, collection_name: str) -> None:
    """Delete a collection by its name."""
    if await collection_exists(client, collection_name):
        await client.delete_collection(collection_name=collection_name)
        logger.info("Deleted collection - %s", collection_name)
    else:
        logger.info("Collection not found - %s", collection_name)


async def upsert_points(
    client: AsyncQdrantClient,
    collection_name: str,
    points: list[models.PointStruct],
    batch_size: int = 100,
) -> None:
    """Upsert points to Qdrant in batches."""
    total_points = len(points)

    for i in range(0, total_points, batch_size):
        batch = points[i : i + batch_size]
        await client.upsert(
            collection_name=collection_name,
            points=batch,
        )
        logger.info(
            "Upserted batch %d-%d of %d points to collection %s",
            i + 1,
            min(i + batch_size, total_points),
            total_points,
            collection_name,
        )


async def search_collection(
    client: AsyncQdrantClient,
    collection_name: str,
    query_vector: list[float],
    limit: int = 10,
    score_threshold: float | None = None,
    must_filters: list[dict[str, Any]] | None = None,
    should_filters: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Search a Qdrant collection with optional filters."""
    filter_dict = {}

    if must_filters:
        filter_dict["must"] = must_filters

    if should_filters:
        filter_dict["should"] = should_filters

    search_result = await client.search(
        collection_name=collection_name,
        query_vector=query_vector,
        limit=limit,
        score_threshold=score_threshold,
        query_filter=filter_dict if filter_dict else None,
        with_payload=True,
    )

    results = []
    for point in search_result:
        result = {
            "id": point.id,
            "score": point.score,
            "payload": point.payload,
        }
        results.append(result)

    return results


async def initialize_qdrant_collections(
    client: AsyncQdrantClient,
    settings: ParliamentMCPSettings,
) -> None:
    """Initialize Qdrant with proper collections.

    This function abstracts the common initialization logic used by both
    the CLI and test fixtures.

    Args:
        client: AsyncQdrantClient instance
        settings: ParliamentMCPSettings instance
    """
    logger.info("Initializing Qdrant collections")

    # Create collections with appropriate vector dimensions
    await create_collection_if_none(
        client,
        settings.PARLIAMENTARY_QUESTIONS_COLLECTION,
        settings.EMBEDDING_DIMENSIONS,
    )

    await create_collection_if_none(
        client,
        settings.HANSARD_CONTRIBUTIONS_COLLECTION,
        settings.EMBEDDING_DIMENSIONS,
    )

    logger.info("Qdrant initialization complete.")


async def create_collection_indicies(client: AsyncQdrantClient, settings: ParliamentMCPSettings) -> None:
    """Create indicies for Qdrant collections."""
    logger.info("Creating indicies for Qdrant collections")

    # Parliamentary Questions
    await client.create_payload_index(
        collection_name=settings.PARLIAMENTARY_QUESTIONS_COLLECTION,
        field_name="dateTabled",
        field_type=models.DatetimeIndexParams(
            type=models.DatetimeIndexType.DATETIME,
            on_disk=True,
        ),
        wait=False,
    )

    await client.create_payload_index(
        collection_name=settings.PARLIAMENTARY_QUESTIONS_COLLECTION,
        field_name="dateAnswered",
        field_type=models.DatetimeIndexParams(
            type=models.DatetimeIndexType.DATETIME,
            on_disk=True,
        ),
        wait=False,
    )

    await client.create_payload_index(
        collection_name=settings.PARLIAMENTARY_QUESTIONS_COLLECTION,
        field_name="askingMemberId",
        field_type=models.IntegerIndexParams(
            type=models.IntegerIndexType.INTEGER,
            on_disk=True,
        ),
        wait=False,
    )

    await client.create_payload_index(
        collection_name=settings.PARLIAMENTARY_QUESTIONS_COLLECTION,
        field_name="house",
        field_type=models.KeywordIndexParams(
            type=models.KeywordIndexType.KEYWORD,
            on_disk=True,
        ),
        wait=False,
    )

    # Hansard Contributions

    await client.create_payload_index(
        collection_name=settings.HANSARD_CONTRIBUTIONS_COLLECTION,
        field_name="SittingDate",
        field_type=models.DatetimeIndexParams(
            type=models.DatetimeIndexType.DATETIME,
            on_disk=True,
        ),
        wait=False,
    )

    await client.create_payload_index(
        collection_name=settings.HANSARD_CONTRIBUTIONS_COLLECTION,
        field_name="DebateSectionExtId",
        field_type=models.KeywordIndexParams(
            type=models.KeywordIndexType.KEYWORD,
            on_disk=True,
        ),
        wait=False,
    )

    await client.create_payload_index(
        collection_name=settings.HANSARD_CONTRIBUTIONS_COLLECTION,
        field_name="MemberId",
        field_type=models.IntegerIndexParams(
            type=models.IntegerIndexType.INTEGER,
            on_disk=True,
        ),
        wait=False,
    )

    await client.create_payload_index(
        collection_name=settings.HANSARD_CONTRIBUTIONS_COLLECTION,
        field_name="House",
        field_type=models.KeywordIndexParams(
            type=models.KeywordIndexType.KEYWORD,
            on_disk=True,
        ),
        wait=False,
    )
