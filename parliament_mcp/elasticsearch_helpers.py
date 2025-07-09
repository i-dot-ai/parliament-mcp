import contextlib
import logging
from collections.abc import AsyncGenerator

from elasticsearch import AsyncElasticsearch, NotFoundError

from parliament_mcp.settings import ParliamentMCPSettings

logger = logging.getLogger(__name__)


@contextlib.asynccontextmanager
async def get_async_es_client(settings: ParliamentMCPSettings) -> AsyncGenerator[AsyncElasticsearch]:
    """Gets an async Elasticsearch client from environment variables.

    Supports both Elastic Cloud (via cloud_id and api_key) and
    direct host/port connections.
    """
    # Prefer cloud connection if cloud_id and api_key are provided
    if settings.ELASTICSEARCH_CLOUD_ID and settings.ELASTICSEARCH_API_KEY:
        logger.info(
            "Connecting to Elasticsearch Cloud with cloud_id: %s",
            settings.ELASTICSEARCH_CLOUD_ID,
        )
        yield AsyncElasticsearch(
            cloud_id=settings.ELASTICSEARCH_CLOUD_ID,
            api_key=settings.ELASTICSEARCH_API_KEY,
            request_timeout=30,
            node_class="httpxasync",
        )
    # Fall back to host/port connection
    else:
        logger.info(
            "Connecting to Elasticsearch at %s://%s:%s",
            settings.ELASTICSEARCH_SCHEME,
            settings.ELASTICSEARCH_HOST,
            settings.ELASTICSEARCH_PORT,
        )
        yield AsyncElasticsearch(
            hosts=[
                {
                    "scheme": settings.ELASTICSEARCH_SCHEME,
                    "host": settings.ELASTICSEARCH_HOST,
                    "port": settings.ELASTICSEARCH_PORT,
                }
            ],
            request_timeout=30,
            node_class="httpxasync",
        )


async def index_exists(es_client: AsyncElasticsearch, index_name: str) -> bool:
    """Checks if an index exists in Elasticsearch."""
    return await es_client.indices.exists(index=index_name)


async def inference_exists(es_client: AsyncElasticsearch, inference_id: str) -> bool:
    """Checks if an inference endpoint exists in Elasticsearch."""
    try:
        await es_client.inference.get(inference_id=inference_id)
    except NotFoundError:
        return False
    else:
        return True


async def create_index_if_none(
    es_client: AsyncElasticsearch, index_name: str, mappings: dict | str | None = None, replicas: int = 1
):
    """Create Elasticsearch index if it doesn't exist."""

    logger.info("Creating index - %s", index_name)

    if not await index_exists(es_client, index_name):
        await es_client.indices.create(
            index=index_name,
            mappings=mappings,
            settings={"number_of_replicas": replicas},
        )
        logger.info("Created index - %s", index_name)
    else:
        logger.info("Index already exists - %s", index_name)


async def create_embedding_inference_endpoint_if_none(
    es_client: AsyncElasticsearch,
    settings: ParliamentMCPSettings,
) -> dict:
    """
    Create an inference endpoint in Elasticsearch.

    Args:
        inference_id (str): The ID for the inference endpoint
        task_type (str): The type of task (default: "text_embedding")
        service (str): The service to use (default: "azureopenai")
        service_settings (dict): Configuration for the service

    Returns:
        dict: The response from Elasticsearch
    """

    if await inference_exists(es_client, settings.EMBEDDING_INFERENCE_ENDPOINT_NAME):
        logger.info("Inference endpoint already exists - %s", settings.EMBEDDING_INFERENCE_ENDPOINT_NAME)
        return None

    try:
        response = await es_client.inference.put(
            inference_id=settings.EMBEDDING_INFERENCE_ENDPOINT_NAME,
            task_type="text_embedding",
            inference_config={
                "service": "azureopenai",
                "service_settings": {
                    "api_key": settings.AZURE_OPENAI_API_KEY,
                    "resource_name": settings.AZURE_OPENAI_RESOURCE_NAME,
                    "deployment_id": settings.AZURE_OPENAI_EMBEDDING_MODEL,
                    "api_version": settings.AZURE_OPENAI_API_VERSION,
                    "dimensions": settings.EMBEDDING_DIMENSIONS,
                },
                "chunking_settings": {
                    "max_chunk_size": settings.CHUNK_SIZE,
                    "sentence_overlap": settings.SENTENCE_OVERLAP,
                    "strategy": settings.CHUNK_STRATEGY,
                },
            },
        )
        logger.info("Created inference endpoint - %s", settings.EMBEDDING_INFERENCE_ENDPOINT_NAME)
    except Exception as e:
        logger.exception("Error creating inference endpoint - %s", settings.EMBEDDING_INFERENCE_ENDPOINT_NAME)
        raise e from e
    else:
        return response


async def delete_index_if_exists(es_client: AsyncElasticsearch, index_name: str) -> dict:
    """
    Delete an index by its name.

    Args:
        index_name (str): The name of the index to delete
    """

    if await index_exists(es_client, index_name):
        await es_client.indices.delete(index=index_name)
        logger.info("Deleted index - %s", index_name)
    else:
        logger.info("Index not found - %s", index_name)


async def delete_inference_endpoint_if_exists(es_client: AsyncElasticsearch, inference_id: str) -> None:
    """
    Delete an inference endpoint by its ID.

    Args:
        inference_id (str): The ID of the inference endpoint to delete

    Returns:
        dict: The response from Elasticsearch
    """
    try:
        await es_client.inference.delete(inference_id=inference_id)
        logger.info("Deleted inference endpoint - %s", inference_id)
    except NotFoundError:
        logger.info("Inference endpoint not found - %s", inference_id)


async def initialize_elasticsearch_indices(
    es_client: AsyncElasticsearch,
    settings: ParliamentMCPSettings,
) -> None:
    """
    Initialize Elasticsearch with proper mappings and inference endpoints.

    This function abstracts the common initialization logic used by both
    the CLI and test fixtures.

    Args:
        es_client: AsyncElasticsearch client
        settings: ParliamentMCPSettings instance
    """
    logger.info("Initializing Elasticsearch indices")

    # Create inference endpoints if using semantic text
    await create_embedding_inference_endpoint_if_none(es_client, settings)

    # Define mappings based on whether we're using semantic text
    pq_mapping = {
        "properties": {
            "questionText": {
                "type": "semantic_text",
                "inference_id": settings.EMBEDDING_INFERENCE_ENDPOINT_NAME,
            },
            "answerText": {
                "type": "semantic_text",
                "inference_id": settings.EMBEDDING_INFERENCE_ENDPOINT_NAME,
            },
        }
    }

    hansard_mapping = {
        "properties": {
            "ContributionTextFull": {
                "type": "semantic_text",
                "inference_id": settings.EMBEDDING_INFERENCE_ENDPOINT_NAME,
            },
        }
    }

    # Create indices with appropriate mappings
    await create_index_if_none(
        es_client,
        settings.PARLIAMENTARY_QUESTIONS_INDEX,
        pq_mapping,
        replicas=settings.ELASTICSEARCH_NUMBER_OF_REPLICAS,
    )
    await create_index_if_none(
        es_client,
        settings.HANSARD_CONTRIBUTIONS_INDEX,
        hansard_mapping,
        replicas=settings.ELASTICSEARCH_NUMBER_OF_REPLICAS,
    )

    logger.info("Elasticsearch initialization complete.")
