import argparse
import asyncio
import logging
from datetime import UTC, datetime

import dateparser
import dotenv
from rich.logging import RichHandler

from parliament_mcp.data_loaders import ElasticHansardLoader, ElasticParliamentaryQuestionLoader
from parliament_mcp.elasticsearch_helpers import (
    create_default_index_template_if_none,
    create_embedding_inference_endpoint_if_none,
    create_index_if_none,
    delete_index_if_exists,
    delete_inference_endpoint_if_exists,
    get_async_es_client,
)
from parliament_mcp.settings import ParliamentMCPSettings, settings

logger = logging.getLogger(__name__)

dotenv.load_dotenv()


def configure_logging(level=logging.INFO, use_colors=True):
    """Configure logging for the parliament_mcp package.

    Args:
        level: The logging level (e.g., logging.INFO, logging.WARNING)
        use_colors: Whether to use colored output (requires rich)
    """
    if use_colors:
        logging.basicConfig(level=level, format="%(message)s", handlers=[RichHandler(rich_tracebacks=True)])
    else:
        logging.basicConfig(level=level, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")


async def delete_elasticsearch(settings: ParliamentMCPSettings):
    """Deletes Elasticsearch indices."""
    logger.info("Deleting Elasticsearch indices.")
    async with get_async_es_client(settings) as es_client:
        await delete_index_if_exists(es_client, settings.PARLIAMENTARY_QUESTIONS_INDEX)
        await delete_index_if_exists(es_client, settings.HANSARD_CONTRIBUTIONS_INDEX)
        await delete_inference_endpoint_if_exists(es_client, settings.EMBEDDING_INFERENCE_ENDPOINT_NAME)


async def init_elasticsearch(settings: ParliamentMCPSettings):
    """Initialises Elasticsearch indices."""
    logger.info("Initialising Elasticsearch indices.")
    async with get_async_es_client(settings) as es_client:
        # Set default index template for single-node cluster
        await create_default_index_template_if_none(es_client, settings)

        # Create inference endpoints
        await create_embedding_inference_endpoint_if_none(es_client, settings)

        # Define mappings
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

        # Create indices
        await create_index_if_none(es_client, settings.PARLIAMENTARY_QUESTIONS_INDEX, pq_mapping)
        await create_index_if_none(es_client, settings.HANSARD_CONTRIBUTIONS_INDEX, hansard_mapping)

        logger.info("Elasticsearch initialization complete.")


async def load_data(settings: ParliamentMCPSettings, source: str, from_date: str, to_date: str):
    """Load data from specified source into Elasticsearch within date range."""
    async with get_async_es_client(settings) as elastic_client:
        if source == "hansard":
            loader = ElasticHansardLoader(
                elastic_client=elastic_client, index_name=settings.HANSARD_CONTRIBUTIONS_INDEX
            )
            await loader.load_all_contributions(from_date, to_date)
        elif source == "parliamentary-questions":
            loader = ElasticParliamentaryQuestionLoader(
                elastic_client=elastic_client, index_name=settings.PARLIAMENTARY_QUESTIONS_INDEX
            )
            await loader.load_questions_for_date_range(from_date, to_date)


def main():
    """CLI entry point that parses arguments and runs commands."""
    # Configure logging for CLI usage
    parser = argparse.ArgumentParser(description="Parliament MCP CLI tool.")
    parser.add_argument(
        "--log-level", "--ll", choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"], default="WARNING"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # Sub-parser for the 'init-elasticsearch' command
    subparsers.add_parser("init-elasticsearch", help="Initialise Elasticsearch indices.")
    subparsers.add_parser("delete-elasticsearch", help="Delete Elasticsearch indices.")

    # Sub-parser for the 'load-data' command
    load_data_parser = subparsers.add_parser("load-data", help="Load data from a specified source.")
    load_data_parser.add_argument(
        "source",
        choices=["hansard", "parliamentary-questions"],
        help="The data source to load.",
    )
    load_data_parser.add_argument(
        "--from-date",
        required=True,
        type=dateparser.parse,
        help="Start date. Supports YYYY-MM-DD format or human-readable formats like '3 days ago', '1 week ago', 'yesterday'.",
    )
    load_data_parser.add_argument(
        "--to-date",
        required=False,
        type=dateparser.parse,
        default=datetime.now(UTC).date(),
        help="End date. Supports YYYY-MM-DD format or human-readable formats like 'today', 'yesterday', '1 week ago'. Defaults to today.",
    )

    args = parser.parse_args()

    configure_logging(level=args.log_level)

    if args.command == "init-elasticsearch":
        asyncio.run(init_elasticsearch(settings))
    elif args.command == "delete-elasticsearch":
        asyncio.run(delete_elasticsearch(settings))
    elif args.command == "load-data":
        asyncio.run(
            load_data(
                settings,
                args.source,
                args.from_date.strftime("%Y-%m-%d"),
                args.to_date.strftime("%Y-%m-%d"),
            )
        )


if __name__ == "__main__":
    main()
