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
    # Debug: Log connection parameters
    logger.info("=== Qdrant Connection Debug Info ===")
    logger.info("Environment: %s", settings.ENVIRONMENT)
    logger.info("AWS Region: %s", settings.AWS_REGION)
    logger.info("Project Name: %s", settings._get_project_name())  # noqa: SLF001

    # Access the properties to trigger SSM fetching if needed
    qdrant_url = settings.QDRANT_URL
    qdrant_api_key = settings.QDRANT_API_KEY

    if not qdrant_url:
        msg = "QDRANT_URL is not configured"
        logger.error("%s! Check environment variables or SSM parameters.", msg)
        raise ValueError(msg)

    logger.info("Attempting to connect to Qdrant at: %s", qdrant_url)
    logger.info("API Key configured: %s", "Yes" if qdrant_api_key else "No")

    try:
        client = AsyncQdrantClient(url=qdrant_url, api_key=qdrant_api_key, timeout=30)

        # Test the connection by getting cluster info
        try:
            # This will fail if connection cannot be established
            collections = await client.get_collections()
            logger.info("Successfully connected to Qdrant! Found %d collections", len(collections.collections))
            for collection in collections.collections[:5]:  # Log first 5 collections
                logger.debug("  - Collection: %s", collection.name)
        except Exception:
            logger.exception(
                "Failed to verify Qdrant connection. URL: %s, Has API Key: %s", qdrant_url, bool(qdrant_api_key)
            )
            raise

        yield client
    except Exception as e:
        logger.exception("Failed to create Qdrant client. Exception type: %s", type(e).__name__)
        raise
    finally:
        logger.debug("Closing Qdrant client connection")
        await client.close()


async def collection_exists(client: AsyncQdrantClient, collection_name: str) -> bool:
    """Checks if a collection exists in Qdrant."""
    return await client.collection_exists(collection_name)


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
