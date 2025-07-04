import argparse
import contextlib

import uvicorn
from fastapi import FastAPI

from mcp_server.app.api import mcp_server, settings


def create_app():
    """Create and configure FastAPI application with MCP server integration."""

    @contextlib.asynccontextmanager
    async def lifespan(_app: FastAPI):
        async with contextlib.AsyncExitStack() as stack:
            await stack.enter_async_context(mcp_server.session_manager.run())
            yield

    app = FastAPI(lifespan=lifespan)
    app.mount(settings.MCP_ROOT_PATH, mcp_server.streamable_http_app())

    return app


def main():
    """Run MCP server with configurable reload option."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-reload", dest="reload", action="store_false")
    parser.set_defaults(reload=True)
    args = parser.parse_args()

    uvicorn.run(
        "mcp_server.app.main:create_app",
        host=settings.MCP_HOST,
        port=settings.MCP_PORT,
        reload=args.reload,
        factory=True,
        timeout_graceful_shutdown=0,
    )


if __name__ == "__main__":
    main()
