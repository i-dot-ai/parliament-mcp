"""Parliament MCP - A Model Context Protocol server for UK Parliament data."""

import os
from importlib.metadata import PackageNotFoundError, version

from parliament_mcp.cli import configure_logging

# Try to get version from package metadata first
try:
    _package_version = version("parliament-mcp")
except PackageNotFoundError:
    _package_version = "unknown"

# Read version from environment variable, fallback to package version
__version__ = os.environ.get("APP_VERSION", _package_version)

__all__ = ["__version__", "configure_logging"]
