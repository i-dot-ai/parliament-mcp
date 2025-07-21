import asyncio
import logging
import os
from datetime import UTC, datetime, timedelta
from typing import Any

from parliament_mcp.cli import configure_logging, load_data
from parliament_mcp.elasticsearch_helpers import get_async_es_client
from parliament_mcp.settings import ParliamentMCPSettings, settings

# Configure logging
logger = logging.getLogger(__name__)
log_level = os.environ.get("LOG_LEVEL", "INFO")
logger.setLevel(log_level)
configure_logging(level=log_level)


async def main(settings: ParliamentMCPSettings, from_date_str: str, to_date_str: str) -> None:
    """Main ingestion function that processes all data sources."""

    logger.info("Ingesting Hansard data...")
    async with get_async_es_client(settings) as es_client:
        await load_data(
            es_client=es_client,
            settings=settings,
            source="hansard",
            from_date=from_date_str,
            to_date=to_date_str,
        )
        logger.info("Hansard data ingestion complete.")

        logger.info("Ingesting Parliamentary Questions data...")
        await load_data(
            es_client=es_client,
            settings=settings,
            source="parliamentary-questions",
            from_date=from_date_str,
            to_date=to_date_str,
        )
        logger.info("Parliamentary Questions data ingestion complete.")


def handler(event: dict, _: Any) -> None:
    """
    AWS Lambda handler function.

    This function is the entry point for the Lambda execution.
    It triggers the daily data ingestion for the Parliament MCP.

    Args:
        event (dict): Lambda event. Should be in the format:
            {
                "from_date": "2024-10-10",  # Optional
                "to_date": "2024-10-12",  # Optional
            }
        context (dict): Lambda context.

    Returns:
        None
    """
    logger.info("Starting daily data ingestion...")

    try:
        utc_now = datetime.now(UTC)

        if "to_date" in event:
            to_date_str = event["to_date"]
        else:
            logger.info("No to_date provided, using default of today")
            to_date_str = utc_now.strftime("%Y-%m-%d")

        if "from_date" in event:
            from_date_str = event["from_date"]
        else:
            logger.info("No from_date provided, using default of 2 days ago")
            from_date_str = (utc_now - timedelta(days=2)).strftime("%Y-%m-%d")

        logger.info("Ingesting data from %s to %s", from_date_str, to_date_str)

        asyncio.run(main(settings, from_date_str, to_date_str))
        logger.info("Daily data ingestion finished successfully.")

    except Exception:
        logger.exception("An error occurred during data ingestion")
        raise
