#!/usr/bin/env python

"""
Transfer Parliamentary Questions and Hansard contributions from Elasticsearch to Qdrant.

Usage:
    uv sync --group transfer-from-es
    uv run python scripts/es_to_qdrant_etl.py pqs --limit 100 --batch-size 100
    uv run python scripts/es_to_qdrant_etl.py hansard --limit 100 --batch-size 100
"""

import asyncio
import logging
import os

import click
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
    },
    "hansard": {
        "es_index": "parliament_mcp_hansard_contributions",
        "qdrant_collection": settings.HANSARD_CONTRIBUTIONS_COLLECTION,
        "model": Contribution,
        "text_fields": ["ContributionTextFull"],
        "excludes": ["ContributionTextFull.inference", "document_uri", "contribution_url", "debate_url"],
        "description": "Hansard contributions",
    },
}


def get_es_client():
    return AsyncElasticsearch(
        cloud_id=os.environ["ELASTICSEARCH_CLOUD_ID"],
        api_key=os.environ["ELASTICSEARCH_API_KEY"],
    )


async def es_batch_generator(es, config_data, batch_size=100, limit=None):
    """Async generator that yields transformed document batches from Elasticsearch."""
    processed = 0

    # Start scrolling
    resp = await es.search(
        index=config_data["es_index"],
        scroll="5m",
        body={
            "query": {"match_all": {}},
            "_source": {"excludes": config_data["excludes"]},
            "size": batch_size,
        },
    )

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
            resp = await es.scroll(scroll_id=scroll_id, scroll="5m")
            hits = resp["hits"]["hits"]
    finally:
        # Clean up scroll
        await es.clear_scroll(scroll_id=scroll_id)


async def transfer_documents(doc_type, limit=None, batch_size=100, concurrent_workers=3):
    """Generic document transfer from Elasticsearch to Qdrant with concurrent batch processing."""
    config_data = CONFIGS[doc_type]

    async with get_async_qdrant_client(settings=settings) as qdrant, get_es_client() as es:
        loader = QdrantDataLoader(qdrant, config_data["qdrant_collection"], settings)

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

        async def worker(progress_task):
            """Worker that processes batches from the queue."""
            while True:
                batch = await queue.get()
                if batch is None:
                    break

                await loader.store_in_qdrant_batch(batch)
                progress.update(progress_task, advance=len(batch))

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
                    tg.create_task(worker(task))

        await es.close()

    logger.info("✓ Transferred %s", config_data["description"])


@click.group()
def cli():
    """ES to Qdrant ETL for Parliament data."""


@cli.command()
@click.option("--limit", type=int, help="Limit docs to transfer")
@click.option("--batch-size", type=int, default=100)
@click.option("--concurrent-workers", type=int, default=3, help="Number of concurrent workers")
def pqs(limit, batch_size, concurrent_workers):
    """Transfer Parliamentary Questions."""
    asyncio.run(transfer_documents("pqs", limit, batch_size, concurrent_workers))


@cli.command()
@click.option("--limit", type=int, help="Limit docs to transfer")
@click.option("--batch-size", type=int, default=100)
@click.option("--concurrent-workers", type=int, default=3, help="Number of concurrent workers")
def hansard(limit, batch_size, concurrent_workers):
    """Transfer Hansard contributions."""
    asyncio.run(transfer_documents("hansard", limit, batch_size, concurrent_workers))


if __name__ == "__main__":
    cli()
