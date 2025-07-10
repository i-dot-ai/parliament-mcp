import argparse
import asyncio
import logging
from datetime import UTC, datetime

import dateparser
import dotenv
from elasticsearch import AsyncElasticsearch
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


async def delete_elasticsearch(es_client: AsyncElasticsearch, settings: ParliamentMCPSettings):
    """Deletes Elasticsearch indices."""
    logger.info("Deleting Elasticsearch indices.")
    await delete_index_if_exists(es_client, settings.PARLIAMENTARY_QUESTIONS_INDEX)
    await delete_index_if_exists(es_client, settings.HANSARD_CONTRIBUTIONS_INDEX)
    await delete_inference_endpoint_if_exists(es_client, settings.EMBEDDING_INFERENCE_ENDPOINT_NAME)


async def init_elasticsearch(es_client: AsyncElasticsearch, settings: ParliamentMCPSettings):
    """Initialises Elasticsearch indices."""
    logger.info("Initialising Elasticsearch indices.")
    await initialize_elasticsearch_indices(es_client, settings)


def create_parser():
    """Create and return the argument parser."""
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

    # Sub-parser for the 'serve' command
    serve_parser = subparsers.add_parser("serve", help="Run the MCP server.")
    serve_parser.add_argument(
        "--no-reload", dest="reload", action="store_false", help="Disable auto-reload in development."
    )
    serve_parser.set_defaults(reload=True)

    return parser


async def async_cli_main(args):
    """Handle async CLI commands."""
    async with get_async_es_client(settings) as es_client:
        if args.command == "init-elasticsearch":
            await init_elasticsearch(es_client, settings)
        elif args.command == "delete-elasticsearch":
            await delete_elasticsearch(es_client, settings)
        elif args.command == "load-data":
            await load_data(
                es_client,
                settings,
                args.source,
                args.from_date.strftime("%Y-%m-%d"),
                args.to_date.strftime("%Y-%m-%d"),
            )


def main():
    """CLI entry point."""
    parser = create_parser()
    args = parser.parse_args()
    configure_logging(level=args.log_level)

    if args.command == "serve":
        # Import here to avoid unnecessary dependencies
        from parliament_mcp.mcp_server.main import main as mcp_main

        mcp_main(reload=args.reload)
    else:
        # Handle async commands
        asyncio.run(async_cli_main(args))


if __name__ == "__main__":
    main()
