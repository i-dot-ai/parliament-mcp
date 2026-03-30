import logging
from typing import Any, Literal

from mcp.server.fastmcp.server import FastMCP
from pydantic import Field

from .utils import log_tool_call, request_bills_api, sanitize_params

logger = logging.getLogger(__name__)

BILL_URL = "https://bills.parliament.uk/bills"


def clean_sitting(sitting: dict) -> str:
    """Extract just the date from a stage sitting."""
    return sitting["date"].split("T")[0]


def clean_stage(stage: dict) -> dict:
    """Extract the useful fields from a bill stage."""
    result = {
        "stage": stage["description"],
        "abbreviation": stage["abbreviation"],
        "house": stage["house"],
    }
    sittings = stage.get("stageSittings", [])
    if sittings:
        result["dates"] = [clean_sitting(s) for s in sittings]
    return result


def clean_bill(bill: dict) -> dict:
    """Shape a bill response for LLM consumption — enough context, minimal noise."""
    result = {
        "billId": bill["billId"],
        "title": bill["shortTitle"],
        "url": f"{BILL_URL}/{bill['billId']}",
        "currentHouse": bill.get("currentHouse"),
        "originatingHouse": bill.get("originatingHouse"),
        "isAct": bill.get("isAct", False),
        "isDefeated": bill.get("isDefeated", False),
    }

    if bill.get("billWithdrawn"):
        result["billWithdrawn"] = bill["billWithdrawn"]

    current_stage = bill.get("currentStage")
    if current_stage:
        result["currentStage"] = clean_stage(current_stage)

    sponsors = bill.get("sponsors", [])
    if sponsors:
        result["sponsors"] = [
            {
                "name": s["member"]["name"],
                "party": s["member"].get("party"),
                "memberFrom": s["member"].get("memberFrom"),
            }
            for s in sponsors
            if s.get("member")
        ]

    return result


@log_tool_call
async def search_bills(
    SearchTerm: str | None = Field(None, description="Search term to find bills by title"),
    CurrentHouse: Literal["Commons", "Lords"] | None = Field(
        None, description="Filter by which house the bill is currently in"
    ),
    OriginatingHouse: Literal["Commons", "Lords"] | None = Field(
        None, description="Filter by which house the bill originated in"
    ),
    IsCurrentBill: bool | None = Field(
        None,
        description="Filter for current bills only (True) or former bills only (False). Default returns all.",
    ),
    ItemsPerPage: int = Field(20, description="Max results to return (default 20)"),
) -> Any:
    """Search for parliamentary bills by title or filter by house and status.

    Returns bill title, current stage, scheduled sitting dates, and sponsors.
    Use this to find out what stage a bill is at, when its next reading is, or who sponsored it.

    Common use cases:
    - Search by title to find a specific bill and its current stage/dates
    - Filter by CurrentHouse to see what bills are before Commons or Lords
    - Set IsCurrentBill=True to see only active bills in the current session
    """
    params = sanitize_params(**locals())

    response = await request_bills_api("/api/v1/Bills", params=params)

    items = response.get("items", [])
    if not items:
        return "No bills found"

    return [clean_bill(bill) for bill in items]


@log_tool_call
async def get_bill_stages(
    bill_id: int = Field(..., description="Bill ID (from search_bills results)"),
) -> Any:
    """Get the full legislative history of a bill — all stages with dates.

    Returns every stage the bill has been through (1st reading, 2nd reading,
    committee, report, 3rd reading, royal assent, etc.) with the dates of
    each sitting. Stages are returned in legislative order.
    """
    response = await request_bills_api(f"/api/v1/Bills/{bill_id}/Stages")

    items = response.get("items", [])
    if not items:
        return "No stages found for this bill"

    items.sort(key=lambda s: s.get("sortOrder", 0))
    return [clean_stage(stage) for stage in items]


def register_bills_tools(mcp_server: FastMCP):
    mcp_server.tool("search_bills")(search_bills)
    mcp_server.tool("get_bill_stages")(get_bill_stages)
