import asyncio
import functools
import json
import logging
import time
from typing import Any

from pydantic.fields import FieldInfo

from parliament_mcp.qdrant_data_loaders import cached_limited_get

logger = logging.getLogger(__name__)

MEMBERS_API_BASE_URL = "https://members-api.parliament.uk"
members_api_semaphore = asyncio.Semaphore(2)


def sanitize_params(**kwargs):
    """
    Sanitize parameters for logging. Remove None values and self.
    """
    params = {}

    for key, value in kwargs.items():
        if key == "self":
            continue
        if value is None or value == "" or isinstance(value, FieldInfo):
            continue
        params[key] = value

    return params


# Decorator for logging MCP tool calls
def log_tool_call(func):
    """Decorator that logs MCP tool calls with execution time and error handling."""

    @functools.wraps(func)
    async def wrapper(*args, **kwargs):
        # Clean parameters for logging
        params = sanitize_params(**kwargs)
        str_params = json.dumps(params, default=str)
        logger.info("Tool %s called with params: %s", func.__name__, str_params)

        # Record start time
        start_time = time.time()

        try:
            result = await func(*args, **kwargs)
            # Calculate and log execution time
            execution_time = time.time() - start_time
            logger.info("Tool `%s` completed in %.3f seconds", func.__name__, execution_time)
        except Exception:
            # Calculate and log execution time even for failed calls
            execution_time = time.time() - start_time
            logger.exception(
                "Exception in tool call `%s` with params %s. Failed after %s seconds",
                func.__name__,
                str_params,
                execution_time,
            )
            raise
        else:
            return result

    return wrapper


def recursive_remove_null_values(obj: Any) -> Any:
    """
    Removes null values from the object.
    """
    if isinstance(obj, dict):
        return {k: recursive_remove_null_values(v) for k, v in obj.items() if v is not None}
    elif isinstance(obj, list):
        return [recursive_remove_null_values(v) for v in obj if v is not None]
    else:
        return obj


def recursive_flatten_links_and_values(obj: Any) -> Any:
    """
    Flattens the structure of the object, removing the links, value, and items keys, and
    replacing them with the actual values.
    """
    if isinstance(obj, dict):
        # Remove links and replace value with its content
        if "links" in obj:
            obj.pop("links")
        if "value" in obj:
            obj = obj["value"]
            # new obj might not be a dict
            return recursive_flatten_links_and_values(obj)
        if "items" in obj:
            obj = obj["items"]
            return recursive_flatten_links_and_values(obj)
        return {k: recursive_flatten_links_and_values(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [recursive_flatten_links_and_values(item) for item in obj]
    else:
        return obj


def remap_values(obj: Any) -> Any:
    """Remaps some commonly used signal values to more interpretable values"""
    remap_dict = {
        "house": {
            1: "Commons",
            2: "Lords",
        },
    }
    if isinstance(obj, dict):
        result = {}
        for k, v in obj.items():
            if k in remap_dict and v in remap_dict[k]:
                result[k] = remap_dict[k][v]
            else:
                result[k] = remap_values(v)
        return result
    elif isinstance(obj, list):
        return [remap_values(item) for item in obj]
    else:
        return obj


# Helper function to make API requests
async def request_members_api(
    endpoint: str,
    params: dict[str, Any] | None = None,
    remove_null_values: bool = False,
    return_string: bool = True,
) -> Any:
    """Make a request to the Parliament API and return JSON response"""
    url = f"{MEMBERS_API_BASE_URL}{endpoint}"
    params = (params or {}) | {"format": "json"}
    logger.info("Requesting members API: %s, %s", url, params)

    async with members_api_semaphore:
        try:
            response = await cached_limited_get(
                url,
                headers={
                    "Accept": "application/json",
                    "User-Agent": "Parlex MCP",
                },
                params=params,
            )
            response.raise_for_status()
            result = response.json()

            result = recursive_flatten_links_and_values(result)

            # Remove blank fields
            if remove_null_values:
                result = recursive_remove_null_values(result)

            result = remap_values(result)

            if return_string:
                return json.dumps(result)
            else:
                return result
        except Exception:
            logger.exception("Exception in request_members_api: %s, %s", url, params)
            raise
