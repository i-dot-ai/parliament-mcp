import argparse
import asyncio
import logging
from datetime import UTC, datetime

import dateparser
import dotenv
from qdrant_client import AsyncQdrantClient
from rich.logging import RichHandler

from parliament_mcp.qdrant_data_loaders import QdrantHansardLoader, QdrantParliamentaryQuestionLoader
from parliament_mcp.qdrant_helpers import (
    create_collection_indicies,
    delete_collection_if_exists,
    get_async_qdrant_client,
    initialize_qdrant_collections,
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


async def delete_qdrant(qdrant_client: AsyncQdrantClient, settings: ParliamentMCPSettings):
    """Deletes Qdrant collections."""
    logger.info("Deleting Qdrant collections.")
    await delete_collection_if_exists(qdrant_client, settings.PARLIAMENTARY_QUESTIONS_COLLECTION)
    await delete_collection_if_exists(qdrant_client, settings.HANSARD_CONTRIBUTIONS_COLLECTION)


async def init_qdrant(qdrant_client: AsyncQdrantClient, settings: ParliamentMCPSettings):
    """Initialises Qdrant collections."""
    logger.info("Initialising Qdrant collections.")
    await initialize_qdrant_collections(qdrant_client, settings)
    await create_collection_indicies(qdrant_client, settings)


def create_parser():
    """Create and return the argument parser."""
    parser = argparse.ArgumentParser(description="Parliament MCP CLI tool.")
    parser.add_argument(
        "--log-level", "--ll", choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"], default="WARNING"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # Sub-parser for the 'init-qdrant' command
    subparsers.add_parser("init-qdrant", help="Initialise Qdrant collections.")
    subparsers.add_parser("delete-qdrant", help="Delete Qdrant collections.")

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


async def load_data(
    qdrant_client: AsyncQdrantClient,
    settings: ParliamentMCPSettings,
    source: str,
    from_date: str,
    to_date: str,
):
    """Load data from specified source into Qdrant."""
    logger.info("Loading %s data from %s to %s", source, from_date, to_date)

    if source == "hansard":
        loader = QdrantHansardLoader(
            qdrant_client=qdrant_client,
            collection_name=settings.HANSARD_CONTRIBUTIONS_COLLECTION,
            settings=settings,
        )
        await loader.load_all_contributions(from_date, to_date)
    elif source == "parliamentary-questions":
        loader = QdrantParliamentaryQuestionLoader(
            qdrant_client=qdrant_client,
            collection_name=settings.PARLIAMENTARY_QUESTIONS_COLLECTION,
            settings=settings,
        )
        await loader.load_questions_for_date_range(from_date, to_date)
    else:
        msg = f"Unknown data source: {source}"
        raise ValueError(msg)


async def async_cli_main(args):
    """Handle async CLI commands."""
    async with get_async_qdrant_client(settings) as qdrant_client:
        if args.command == "init-qdrant":
            await init_qdrant(qdrant_client, settings)
        elif args.command == "delete-qdrant":
            await delete_qdrant(qdrant_client, settings)
        elif args.command == "load-data":
            await load_data(
                qdrant_client,
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
