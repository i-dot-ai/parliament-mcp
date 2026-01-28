import asyncio
import contextlib
import logging
import time

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from parliament_mcp import __version__
from parliament_mcp.mcp_server.api import mcp_server, settings

logger = logging.getLogger(__name__)

# Track last activity time per session (for inactivity-based cleanup)
session_last_activity: dict[str, float] = {}

# Inactivity timeout: 30 minutes
INACTIVITY_TIMEOUT_SECONDS = 30 * 60


async def session_cleanup_task(mcp_server, interval_seconds=60):
    """Periodically clean up terminated and stale MCP sessions.

    The MCP library marks sessions as terminated but doesn't remove them
    from the _server_instances dict, causing memory to grow indefinitely.
    This task removes:
    1. Terminated sessions (is_terminated == True)
    2. Stale sessions (no activity for INACTIVITY_TIMEOUT_SECONDS)
    """
    while True:
        await asyncio.sleep(interval_seconds)
        sm = mcp_server.session_manager
        if hasattr(sm, "_server_instances"):
            now = time.time()
            sessions_to_remove = []

            # Accessing private _server_instances is intentional - working around MCP library bug
            # where terminated sessions are not removed from memory
            for sid, transport in sm._server_instances.items():  # noqa: SLF001
                # Remove terminated sessions
                if transport.is_terminated:
                    sessions_to_remove.append((sid, "terminated"))
                    continue

                # Initialize tracking for sessions we haven't seen yet
                if sid not in session_last_activity:
                    session_last_activity[sid] = now
                    continue

                # Remove sessions inactive for too long
                if now - session_last_activity[sid] > INACTIVITY_TIMEOUT_SECONDS:
                    sessions_to_remove.append((sid, "inactive"))

            for sid, reason in sessions_to_remove:
                transport = sm._server_instances[sid]  # noqa: SLF001
                if reason == "inactive":
                    # Terminate inactive sessions to close their streams and stop their tasks
                    await transport.terminate()
                del sm._server_instances[sid]  # noqa: SLF001
                session_last_activity.pop(sid, None)  # Clean up activity tracking
                logger.info("Cleaned up %s session: %s", reason, sid)


def create_app():
    """Create and configure FastAPI application with MCP server integration."""

    @contextlib.asynccontextmanager
    async def lifespan(_app: FastAPI):
        async with contextlib.AsyncExitStack() as stack:
            await stack.enter_async_context(mcp_server.session_manager.run())
            cleanup_task = asyncio.create_task(session_cleanup_task(mcp_server))
            try:
                yield
            finally:
                cleanup_task.cancel()

    app = FastAPI(lifespan=lifespan)

    @app.middleware("http")
    async def track_session_activity(request: Request, call_next):
        """Track last activity time for MCP sessions."""
        response = await call_next(request)
        # Update activity timestamp for this session
        session_id = request.headers.get("mcp-session-id")
        if session_id:
            session_last_activity[session_id] = time.time()
        return response

    @app.get("/healthcheck")
    async def health_check():
        sm = mcp_server.session_manager
        session_count = len(sm._server_instances) if hasattr(sm, "_server_instances") else 0  # noqa: SLF001
        return JSONResponse(
            status_code=200,
            content={
                "status": "ok",
                "version": __version__,
                "active_sessions": session_count,
            },
        )

    app.mount(settings.MCP_ROOT_PATH, mcp_server.streamable_http_app())

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
