import argparse
import asyncio
import logging
from datetime import UTC, datetime

import dateparser
import dotenv
from rich.logging import RichHandler

from parliament_mcp.data_loaders import load_data
from parliament_mcp.elasticsearch_helpers import (
    delete_index_if_exists,
    delete_inference_endpoint_if_exists,
    get_async_es_client,
    initialize_elasticsearch_indices,
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
        await initialize_elasticsearch_indices(es_client, settings)


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
