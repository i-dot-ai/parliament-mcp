"""REST API that exposes every MCP tool as `POST /api/v1/tools/{name}`.

Lets clients hit tools without the MCP session/JSON-RPC handshake — useful for
curl, scripts, and anything that doesn't speak MCP.
"""

from typing import Any

from fastapi import APIRouter, HTTPException
from mcp.server.lowlevel.server import request_ctx
from mcp.shared.context import RequestContext

from .api import mcp_server
from .resources import get_shared_resources

router = APIRouter(prefix="/api/v1", tags=["rest"])


@router.get("/tools")
async def list_tools() -> list[dict[str, Any]]:
    """List every available tool with its name, description, and JSON input schema."""
    tools = await mcp_server.list_tools()
    return [{"name": t.name, "description": t.description, "inputSchema": t.inputSchema} for t in tools]


@router.post("/tools/{tool_name}")
async def call_tool(tool_name: str, arguments: dict[str, Any] | None = None) -> Any:
    """Invoke an MCP tool. POST a JSON object of the tool's named arguments."""
    tool = mcp_server._tool_manager.get_tool(tool_name)  # noqa: SLF001
    if tool is None:
        raise HTTPException(status_code=404, detail=f"Unknown tool: {tool_name}")

    # Tool bodies call mcp_server.get_context() to grab the shared QdrantQueryHandler;
    # outside an MCP session we set request_ctx ourselves with the shared resources.
    stub = RequestContext(
        request_id=f"rest-{tool_name}",
        meta=None,
        session=None,  # tools in this codebase don't touch the session
        lifespan_context=get_shared_resources(),
    )
    token = request_ctx.set(stub)
    try:
        return await tool.run(arguments or {}, context=mcp_server.get_context(), convert_result=False)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        request_ctx.reset(token)
