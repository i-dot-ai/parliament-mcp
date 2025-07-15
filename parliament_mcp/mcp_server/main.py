import contextlib

import uvicorn
from fastapi import FastAPI
from fastapi.responses import JSONResponse

from parliament_mcp.mcp_server.api import mcp_server, settings


def create_app():
    """Create and configure FastAPI application with MCP server integration."""

    @contextlib.asynccontextmanager
    async def lifespan(_app: FastAPI):
        async with contextlib.AsyncExitStack() as stack:
            await stack.enter_async_context(mcp_server.session_manager.run())
            yield

    app = FastAPI(lifespan=lifespan)

    @app.get("/healthcheck")
    async def health_check():
        return JSONResponse(status_code=200, content={"status": "ok"})

    app.mount(settings.MCP_ROOT_PATH, mcp_server.streamable_http_app())

    @app.get("/healthcheck")
    async def health_check():
        return JSONResponse(status_code=200, content={"status": "ok"})

    return app


def main(reload=True):
    """Run MCP server with configurable reload option."""
    uvicorn.run(
        "parliament_mcp.mcp_server.main:create_app",
        host=settings.MCP_HOST,
        port=settings.MCP_PORT,
        reload=reload,
        factory=True,
        timeout_graceful_shutdown=0,
    )


if __name__ == "__main__":
    main()
