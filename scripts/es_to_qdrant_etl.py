#!/usr/bin/env -S uv run --script
#
# /// script
# requires-python = ">=3.12"
# dependencies = ["parliament-mcp"]
# ///
# [tool.uv.sources]
# parliament-mcp = { path = "..", group = "transfer-from-es" }

"""
Transfer Parliamentary Questions and Hansard contributions from Elasticsearch to Qdrant.

Usage:
    ./scripts/es_to_qdrant_etl.py pqs --limit 100 --batch-size 100
    ./scripts/es_to_qdrant_etl.py hansard --limit 100 --batch-size 100
"""

import asyncio
import logging

import click
from dotenv import dotenv_values
from elasticsearch import AsyncElasticsearch
from rich.progress import Progress

from parliament_mcp.models import Contribution, ParliamentaryQuestion
from parliament_mcp.qdrant_data_loaders import QdrantDataLoader
from parliament_mcp.qdrant_helpers import get_async_qdrant_client
from parliament_mcp.settings import settings

logger = logging.getLogger(__name__)

config = dotenv_values()


async def transfer_pqs(limit=None, batch_size=100):
    """Transfer Parliamentary Questions from ES to Qdrant."""

    es_index_name = "lex-parliamentary-questions-prod-may-2025"

    es = AsyncElasticsearch(cloud_id=config["ELASTICSEARCH_CLOUD_ID"], api_key=config["ELASTICSEARCH_API_KEY"])
    async with get_async_qdrant_client(settings=settings) as qdrant_client:
        loader = QdrantDataLoader(qdrant_client, settings.PARLIAMENTARY_QUESTIONS_COLLECTION, settings)

        # Get total count
        total = (await es.count(index=es_index_name))["count"]
        if limit:
            total = min(total, limit)

        # Start scrolling
        resp = await es.search(
            index=es_index_name,
            scroll="5m",
            body={
                "query": {"match_all": {}},
                "_source": {"excludes": ["questionText.inference", "answerText.inference", "document_uri"]},
                "size": batch_size,
            },
        )

        scroll_id = resp["_scroll_id"]
        hits = resp["hits"]["hits"]
        processed = 0

        with Progress() as progress:
            task = progress.add_task("Transferring PQs...", total=total)
            while hits and (not limit or processed < limit):
                # Transform hits
                docs = []
                for hit in hits:
                    doc = hit["_source"]
                    # Handle nested text fields
                    doc["questionText"] = doc["questionText"]["text"]
                    doc["answerText"] = doc["answerText"]["text"]
                    docs.append(ParliamentaryQuestion.model_validate(doc))

                # Store batch
                await loader.store_in_qdrant_batch(docs)

                processed += len(hits)
                progress.update(task, advance=len(hits))

                # Next batch
                resp = await es.scroll(scroll_id=scroll_id, scroll="5m")
                hits = resp["hits"]["hits"]

        await es.clear_scroll(scroll_id=scroll_id)
        await es.close()

    logger.info("✓ Transferred %s PQs", processed)


async def transfer_hansard(limit=None, batch_size=100):
    """Transfer Hansard contributions from ES to Qdrant."""
    es_index_name = "parliament_mcp_hansard_contributions"
    qdrant_collection_name = "parliament_mcp_hansard_contributions"

    es = AsyncElasticsearch(cloud_id=config["ELASTICSEARCH_CLOUD_ID"], api_key=config["ELASTICSEARCH_API_KEY"])
    async with get_async_qdrant_client(settings=settings) as qdrant_client:
        loader = QdrantDataLoader(qdrant_client, qdrant_collection_name, settings)

        # Get total count
        total = (await es.count(index=es_index_name))["count"]
        if limit:
            total = min(total, limit)

        # Start scrolling
        resp = await es.search(
            index=es_index_name,
            scroll="5m",
            size=batch_size,
            body={
                "query": {"match_all": {}},
                "_source": {
                    "excludes": ["ContributionTextFull.inference", "document_uri", "contribution_url", "debate_url"]
                },
            },
        )

        scroll_id = resp["_scroll_id"]
        hits = resp["hits"]["hits"]
        processed = 0

        with Progress() as progress:
            task = progress.add_task("Transferring Hansard...", total=total)
            while hits and (not limit or processed < limit):
                # Transform hits
                docs = []
                for hit in hits:
                    doc = hit["_source"]
                    # Handle nested text fields
                    doc["ContributionTextFull"] = doc["ContributionTextFull"]["text"]
                    docs.append(Contribution.model_validate(doc))

                # Store batch
                await loader.store_in_qdrant_batch(docs)

                processed += len(hits)
                progress.update(task, advance=len(hits))

                # Next batch
                resp = await es.scroll(scroll_id=scroll_id, scroll="5m")
                hits = resp["hits"]["hits"]

        await es.clear_scroll(scroll_id=scroll_id)
        await es.close()

    logger.info("✓ Transferred %s Hansard contributions", processed)


@click.group()
def cli():
    """ES to Qdrant ETL for Parliament data."""


@cli.command()
@click.option("--limit", type=int, help="Limit docs to transfer")
@click.option("--batch-size", type=int, default=100)
def pqs(limit, batch_size):
    """Transfer Parliamentary Questions."""
    asyncio.run(transfer_pqs(limit, batch_size))


@cli.command()
@click.option("--limit", type=int, help="Limit docs to transfer")
@click.option("--batch-size", type=int, default=100)
def hansard(limit, batch_size):
    """Transfer Hansard contributions."""
    asyncio.run(transfer_hansard(limit, batch_size))


if __name__ == "__main__":
    cli()
