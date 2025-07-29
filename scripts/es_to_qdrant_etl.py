#!/usr/bin/env python

"""
Transfer Parliamentary Questions and Hansard contributions from Elasticsearch to Qdrant.

Usage:
    uv sync --group transfer-from-es
    uv run python scripts/es_to_qdrant_etl.py pqs --limit 100 --batch-size 100
    uv run python scripts/es_to_qdrant_etl.py hansard --limit 100 --batch-size 100
"""

import asyncio
import contextlib
import logging
import os

import click
from bloom_filter import BloomFilter
from dotenv import load_dotenv
from elasticsearch import AsyncElasticsearch
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TaskProgressColumn,
    TextColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
)
from tenacity import retry, stop_after_attempt, wait_exponential

from parliament_mcp.models import Contribution, ParliamentaryQuestion
from parliament_mcp.qdrant_data_loaders import QdrantDataLoader
from parliament_mcp.qdrant_helpers import get_async_qdrant_client
from parliament_mcp.settings import settings

logger = logging.getLogger(__name__)

load_dotenv()

# Configuration for each document type
CONFIGS = {
    "pqs": {
        "es_index": "lex-parliamentary-questions-prod-may-2025",
        "qdrant_collection": settings.PARLIAMENTARY_QUESTIONS_COLLECTION,
        "model": ParliamentaryQuestion,
        "text_fields": ["questionText", "answerText"],
        "excludes": ["questionText.inference", "answerText.inference", "document_uri"],
        "description": "PQs",
        "expected_documents": 600000,  # 593,281 documents
    },
    "hansard": {
        "es_index": "parliament_mcp_hansard_contributions",
        "qdrant_collection": settings.HANSARD_CONTRIBUTIONS_COLLECTION,
        "model": Contribution,
        "text_fields": ["ContributionTextFull"],
        "excludes": ["ContributionTextFull.inference", "document_uri", "contribution_url", "debate_url"],
        "description": "Hansard contributions",
        "expected_documents": 2400000,  # 2,389,496 documents
    },
}


def get_es_client():
    return AsyncElasticsearch(
        cloud_id=os.environ["ELASTICSEARCH_CLOUD_ID"],
        api_key=os.environ["ELASTICSEARCH_API_KEY"],
    )


async def populate_bloom_filter_from_qdrant(qdrant, collection_name, expected_items=1000000):
    """Populate a bloom filter with existing chunk IDs from Qdrant."""
    bloom = BloomFilter(max_elements=expected_items, error_rate=0.01)

    # Get collection info to know total points
    collection_info = await qdrant.get_collection(collection_name)
    total_points = collection_info.points_count

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
        TaskProgressColumn(),
        TextColumn("Elapsed: "),
        TimeElapsedColumn(),
        TimeRemainingColumn(),
        expand=True,
    ) as progress:
        task = progress.add_task(
            f"Constructing bloom filter from existing chunks in {collection_name}...", total=total_points
        )

        # Scroll through all points in the collection
        offset = None
        total_loaded = 0

        while True:
            result = await qdrant.scroll(
                collection_name=collection_name,
                limit=1000,  # Process in batches
                offset=offset,
                with_payload=["chunk_id"],  # Only fetch the chunk_id field
                with_vectors=False,  # Don't fetch vectors to save bandwidth
            )

            points, offset = result

            if not points:
                break

            # Add chunk IDs to bloom filter
            for point in points:
                if "chunk_id" in point.payload:
                    bloom.add(point.payload["chunk_id"])

            total_loaded += len(points)
            progress.update(task, advance=len(points))

            if offset is None:
                break

    logger.info("Loaded %d existing chunk IDs into bloom filter", total_loaded)
    return bloom


async def es_batch_generator(es, config_data, batch_size=100, limit=None):
    """Async generator that yields transformed document batches from Elasticsearch."""
    processed = 0

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=10))
    async def es_search_with_retry():
        return await es.search(
            index=config_data["es_index"],
            scroll="5m",
            body={
                "query": {"match_all": {}},
                "_source": {"excludes": config_data["excludes"]},
                "size": batch_size,
            },
        )

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=10))
    async def es_scroll_with_retry(scroll_id):
        return await es.scroll(scroll_id=scroll_id, scroll="5m")

    # Start scrolling
    resp = await es_search_with_retry()
    scroll_id = resp["_scroll_id"]
    hits = resp["hits"]["hits"]

    try:
        while hits and (not limit or processed < limit):
            # Transform batch
            def flatten_doc(hit):
                doc = hit["_source"]
                for field in config_data["text_fields"]:
                    if field in doc and isinstance(doc[field], dict):
                        doc[field] = doc[field]["text"]
                return config_data["model"].model_validate(doc)

            docs = [flatten_doc(hit) for hit in hits]
            processed += len(docs)
            yield docs

            # Get next batch
            resp = await es_scroll_with_retry(scroll_id)
            hits = resp["hits"]["hits"]
    finally:
        with contextlib.suppress(Exception):
            await es.clear_scroll(scroll_id=scroll_id)


async def worker(progress_task, loader, bloom_filter, queue, progress):
    """Worker that processes batches from the queue."""

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=10), reraise=True)
    async def store_batch_with_retry(batch):
        # Filter out documents that already exist if bloom filter is available
        if bloom_filter:
            filtered_batch = []
            for doc in batch:
                # Generate chunk IDs to check against bloom filter
                chunks = list(doc.to_chunks(loader.chunker))
                new_chunks_exist = False

                for chunk in chunks:
                    if chunk["chunk_id"] not in bloom_filter:
                        new_chunks_exist = True
                        break

                if new_chunks_exist:
                    filtered_batch.append(doc)

            if not filtered_batch:
                logger.debug("Skipped batch - all documents already exist")
                return

            batch = filtered_batch

        await loader.store_in_qdrant_batch(batch)

    while True:
        batch = await queue.get()
        if batch is None:
            break

        await store_batch_with_retry(batch)
        progress.update(progress_task, advance=len(batch))


async def transfer_documents(doc_type, limit=None, batch_size=100, concurrent_workers=3, skip_existing=True):
    """Generic document transfer from Elasticsearch to Qdrant with concurrent batch processing."""
    config_data = CONFIGS[doc_type]

    async with get_async_qdrant_client(settings=settings) as qdrant, get_es_client() as es:
        loader = QdrantDataLoader(qdrant, config_data["qdrant_collection"], settings)

        # Load existing chunk IDs if skip_existing is enabled
        bloom_filter = None
        if skip_existing:
            bloom_filter = await populate_bloom_filter_from_qdrant(
                qdrant, config_data["qdrant_collection"], expected_items=config_data["expected_documents"]
            )

        # Get total count
        total = (await es.count(index=config_data["es_index"]))["count"]
        if limit:
            total = min(total, limit)

        queue = asyncio.Queue(maxsize=concurrent_workers * 2)

        async def producer():
            """Produces batches from ES and puts them in the queue."""
            gen = es_batch_generator(es, config_data, batch_size, limit)
            async for batch in gen:
                await queue.put(batch)

            # Signal completion
            for _ in range(concurrent_workers):
                await queue.put(None)

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            MofNCompleteColumn(),
            TaskProgressColumn(),
            TextColumn("Elapsed: "),
            TimeElapsedColumn(),
            TextColumn("Remaining: "),
            TimeRemainingColumn(),
            expand=True,
        ) as progress:
            task = progress.add_task(f"Transferring {config_data['description']}...", total=total)

            # Run producer and workers concurrently
            async with asyncio.TaskGroup() as tg:
                tg.create_task(producer())
                for _ in range(concurrent_workers):
                    tg.create_task(worker(task, loader, bloom_filter, queue, progress))

        await es.close()

    logger.info("✓ Transferred %s", config_data["description"])


@click.group()
def cli():
    """ES to Qdrant ETL for Parliament data."""


@cli.command()
@click.option("--limit", type=int, help="Limit docs to transfer")
@click.option("--batch-size", type=int, default=100)
@click.option("--concurrent-workers", type=int, default=3, help="Number of concurrent workers")
@click.option("--skip-existing/--no-skip-existing", default=True, help="Skip documents that already exist in Qdrant")
def pqs(limit, batch_size, concurrent_workers, skip_existing):
    """Transfer Parliamentary Questions."""
    asyncio.run(transfer_documents("pqs", limit, batch_size, concurrent_workers, skip_existing))


@cli.command()
@click.option("--limit", type=int, help="Limit docs to transfer")
@click.option("--batch-size", type=int, default=100)
@click.option("--concurrent-workers", type=int, default=3, help="Number of concurrent workers")
@click.option("--skip-existing/--no-skip-existing", default=True, help="Skip documents that already exist in Qdrant")
def hansard(limit, batch_size, concurrent_workers, skip_existing):
    """Transfer Hansard contributions."""
    asyncio.run(transfer_documents("hansard", limit, batch_size, concurrent_workers, skip_existing))


if __name__ == "__main__":
    cli()
