import asyncio
import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any, Literal

import sentry_sdk
from mcp.server.fastmcp.server import FastMCP
from pydantic import Field

from parliament_mcp.embedding_helpers import get_openai_client
from parliament_mcp.mcp_server.qdrant_query_handler import QdrantQueryHandler
from parliament_mcp.qdrant_helpers import get_async_qdrant_client
from parliament_mcp.settings import settings

from .utils import clean_posts_list, log_tool_call, request_members_api, sanitize_params

logger = logging.getLogger(__name__)


@asynccontextmanager
async def mcp_lifespan(_server: FastMCP) -> AsyncGenerator[dict]:
    """Manage application lifecycle with type-safe context"""
    # Initialize on startup

    openai_client = get_openai_client(settings)
    async with get_async_qdrant_client(settings) as qdrant_client:
        yield {
            "qdrant_query_handler": QdrantQueryHandler(qdrant_client, openai_client, settings),
        }


mcp_server = FastMCP(name="Parliament MCP Server", lifespan=mcp_lifespan)

# init Sentry if configured
if settings.SENTRY_DSN and settings.ENVIRONMENT in ["dev", "preprod", "prod"] and settings.SENTRY_DSN != "placeholder":
    sentry_sdk.init(
        dsn=settings.SENTRY_DSN,
        traces_sample_rate=1.0,
        profiles_sample_rate=1.0,
    )


@mcp_server.tool("search_constituency")
@log_tool_call
async def search_constituency(
    searchText: str | None = Field(None, description="Search for constituencies by name or text"),
    constituency_id: int | None = Field(None, description="Get comprehensive constituency details by ID"),
    skip: int = Field(0, description="Number of results to skip (for search)"),
    take: int = Field(
        5,
        description="Number of results to take (Max 20, for search). Default 5 (reasonable for most use cases)",
    ),
) -> Any:
    """
    Search for constituencies or get comprehensive constituency details on a single constituency.

    Usage patterns:
    - searchText only: Search for constituencies by name/text
    - constituency_id only: Get comprehensive constituency details including basic info, representations, and synopsis

    Examples:
    - search_constituency(searchText="London") - Search for constituencies containing "London"
    - search_constituency(constituency_id=123) - Get comprehensive constituency details for ID 123
    """
    # Clean parameters to handle FieldInfo objects
    params = sanitize_params(**locals())
    searchText = params.get("searchText")
    constituency_id = params.get("constituency_id")

    # Validate parameter combination
    if searchText is not None and constituency_id is not None:
        msg = "Must provide either searchText or constituency_id, not both"
        logger.error(msg)
        raise ValueError(msg)

    if searchText is None and constituency_id is None:
        msg = "Must provide either searchText or constituency_id"
        logger.error(msg)
        raise ValueError(msg)

    # Search for constituencies
    if searchText is not None:
        return await request_members_api("/api/Location/Constituency/Search", params)

    # Get comprehensive constituency details
    return await request_members_api(f"/api/Location/Constituency/{constituency_id}")


@mcp_server.tool("get_election_results")
@log_tool_call
async def get_election_results(
    constituency_id: int | None = Field(None, description="Constituency ID"),
    election_id: int | None = Field(
        None,
        description="Specific election ID. If not provided, returns the latest election result for the constituency.",
    ),
    member_id: int | None = Field(None, description="Member ID. Search for a specific member's election results."),
) -> Any:
    """
    Get election results for a constituency.

    Args:
        constituency_id: The ID of the constituency
        election_id: Optional specific election ID. If not provided, returns the latest election result.
        member_id: Optional member ID. Search for a specific member's election results.

    Examples:
    - get_election_results(constituency_id=123) - Get latest election result for constituency 123
    - get_election_results(constituency_id=123, election_id=456) - Get specific election result for constituency 123, election 456
    - get_election_results(member_id=456) - Get specific member's latest election result
    """
    if member_id is not None:
        return await request_members_api(f"/api/Members/{member_id}/LatestElectionResult")
    elif constituency_id is not None and election_id is not None:
        return await request_members_api(f"/api/Location/Constituency/{constituency_id}/ElectionResult/{election_id}")
    elif constituency_id is not None:
        return await request_members_api(f"/api/Location/Constituency/{constituency_id}/ElectionResult/Latest")
    else:
        msg = "Must provide either member_id, constituency_id, or election_id"
        logger.error(msg)
        raise ValueError(msg)


@mcp_server.tool("search_members")
@log_tool_call
async def search_members(
    Name: str | None = Field(None, description="Member name"),
    PartyId: int | None = Field(None, description="Party ID"),
    House: Literal["Commons", "Lords"] | None = Field(None, description="House (Commons or Lords)"),
    ConstituencyId: int | None = Field(None, description="Constituency ID"),
    Gender: str | None = Field(None, description="Gender"),
    member_since: str | None = Field(None, description="Was a member on or after date (YYYY-MM-DD)"),
    member_until: str | None = Field(None, description="Was a member on or before date (YYYY-MM-DD)"),
    IsCurrentMember: bool = Field(True, description="Whether the member is currently a member"),
    skip: int = Field(0, description="Number of results to skip"),
    take: int = Field(5, description="Number of results to take (Max 20)"),
) -> Any:
    """
    Search for members of the Commons or Lords by name, post title, or other filters. It is recommended to take at least 5 results.

    Note: Multiple members may have the same name. The user may need to provide additional filters to narrow down the results.

    Returns a list of member details dictionaries. Each dictionary contains details such as:
        - id: ID of the member
        - name: Name of the member
        - latestHouseMembership.membershipFrom: Constituency of the member
        - party: Party of the member
        - house: House of the member (1 = Commons, 2 = Lords)
        - membershipStartDate: Membership started since (YYYY-MM-DD)
        - membershipEndDate: Membership ended since (YYYY-MM-DD)
        - latestParty: Latest party of the member
    """
    params = sanitize_params(**locals())
    return await request_members_api("/api/Members/Search", params)


@mcp_server.tool("get_detailed_member_information")
@log_tool_call
async def get_detailed_member_information(
    member_id: int = Field(..., description="Member ID"),
    include_synopsis: bool = Field(True, description="Include member synopsis"),
    include_biography: bool = Field(
        False,
        description="Include member biography with constituency, election, party, government/opposition posts, and committee memberships",
    ),
    include_contact: bool = Field(False, description="Include contact information"),
    include_registered_interests: bool = Field(
        False,
        description="Include registered interests. Interests are gifts, donations, appointments, etc.",
    ),
    include_voting_record: bool = Field(
        False, description="Include recent voting record for the member's current house"
    ),
) -> Any:
    """Get detailed member information.

    Args:
        member_id: The ID of the member
        include_synopsis: Whether to include member synopsis. Good to include by default.
        include_biography: Whether to include member biography
        include_contact: Whether to include member contact information
        include_registered_interests: Whether to include member registered interests
        include_voting_record: Whether to include member voting record
    """
    # Define the tasks we want to run

    # Execute all tasks concurrently using TaskGroup

    tasks = {}
    async with asyncio.TaskGroup() as tg:
        tasks["member"] = tg.create_task(request_members_api(f"/api/Members/{member_id}"))
        if include_synopsis:
            tasks["synopsis"] = tg.create_task(request_members_api(f"/api/Members/{member_id}/Synopsis"))
        if include_biography:
            tasks["biography"] = tg.create_task(request_members_api(f"/api/Members/{member_id}/Biography"))
        if include_contact:
            tasks["contact"] = tg.create_task(
                request_members_api(
                    f"/api/Members/{member_id}/Contact",
                    remove_null_values=True,
                )
            )
        if include_registered_interests:
            tasks["registered_interests"] = tg.create_task(
                request_members_api(f"/api/Members/{member_id}/RegisteredInterests")
            )

    if include_voting_record:
        async with asyncio.TaskGroup() as tg:
            member_house = tasks["member"].result()["latestHouseMembership"]["house"]
            tasks["voting"] = tg.create_task(
                request_members_api(f"/api/Members/{member_id}/Voting", params={"house": member_house})
            )

    # Build result dictionary, handling any exceptions
    result = {}
    for key, task in tasks.items():
        result[key] = task.result()

    return result


@mcp_server.tool("get_state_of_the_parties")
@log_tool_call
async def get_state_of_the_parties(
    house: Literal["Commons", "Lords"] = Field(..., description="Commons|Lords"),
    forDate: str = Field(..., description="YYYY-MM-DD"),
) -> Any:
    """Get state of the parties for a house on a specific date"""
    return await request_members_api(f"/api/Parties/StateOfTheParties/{house}/{forDate}")


@mcp_server.tool("list_ministerial_roles")
@log_tool_call
async def list_ministerial_roles(
    post_type: Literal["GovernmentPosts", "OppositionPosts"] = "GovernmentPosts", include_all_minsiters=True
) -> Any:
    """List ministerial roles. Exhaustive list of all government posts, and their current holders.

    If include_all_minsiters is True, then the list will include ministers such as Ministers of State and Parliamentary Under-Secretaries of State.
    If include_all_minsiters is False, then the list will only include the Cabinet Ministers.

    Args:
        post_type: The type of post to list.
        include_all_minsiters: Whether to include Ministers of State and Parliamentary Under-Secretaries of State.
    """

    party_info = None

    if include_all_minsiters:
        # Junior ministers are included when querying by departmentId
        posts = []
        departments = await get_departments()

        tasks = []
        async with asyncio.TaskGroup() as tg:
            for department in departments:
                tasks.append(
                    (
                        tg.create_task(
                            request_members_api(f"/api/Posts/{post_type}", params={"departmentId": department["id"]})
                        ),
                        department,
                    )
                )

        for task, department in tasks:
            if department_posts := task.result():
                party_info = department_posts[0]["postHolders"][0]["member"]["latestParty"]
                department_posts = clean_posts_list(department_posts)
                posts.append(
                    {"department": department["name"], "department_id": department["id"], "posts": department_posts}
                )
    else:
        posts = await request_members_api(f"/api/Posts/{post_type}")

        if posts:
            party_info = posts[0]["postHolders"][0]["member"]["latestParty"]

        posts = clean_posts_list(posts)

    return {"posts": posts, "party_info": party_info}


# Reference endpoints
@mcp_server.tool("get_departments")
@log_tool_call
async def get_departments() -> Any:
    """Get departments"""
    results = await request_members_api("/api/Reference/Departments")
    # Leader of HM Official Opposition is a special case not returned by the departments API
    results.append({"id": 107, "name": "Leader of HM Official Opposition"})
    return results


@mcp_server.tool("search_parliamentary_questions")
@log_tool_call
async def search_parliamentary_questions(
    query: str | None = Field(None, description="Search query"),
    dateFrom: str | None = Field(None, description="Start date (YYYY-MM-DD)"),
    dateTo: str | None = Field(None, description="End date (YYYY-MM-DD)"),
    party: str | None = Field(None, description="Party"),
    member_id: int | None = Field(None, description="Member ID"),
    answering_body_name: str | None = Field(
        None,
        description="Answering body name (e.g. 'Department for Transport, Cabinet Office, etc.)",
    ),
) -> Any:
    """
    Search Parliamentary Written Questions (sometimes known as PQs)

    Written questions allow MPs and Members of the House of Lords to ask for information on the work, policy and activities of Government departments, related bodies, and the administration of Parliament.

    Common use case for this function:
    - Provide a query to search for written questions on a specific topic for all time
    - Provide a query and date range to search for written questions on a specific topic in a specific date range
    - Provide a query, date range, party and member name to search for written questions on a specific topic in a specific date range by a specific member of a specific party
    - Provide a member name to search for all written questions by a specific member for all time
    - Provide a member id to search for all written questions by a specific member for all time
    - Provide an answering body name to search for all questions answered by a specific body or department such as 'Department for Transport' or 'Cabinet Office'
    """
    ctx = mcp_server.get_context()
    qdrant_query_handler: QdrantQueryHandler = ctx.request_context.lifespan_context["qdrant_query_handler"]
    result = await qdrant_query_handler.search_parliamentary_questions(
        query=query,
        dateFrom=dateFrom,
        dateTo=dateTo,
        party=party,
        member_id=member_id,
        answering_body_name=answering_body_name,
    )

    if not result:
        return "No results found"

    return result


@mcp_server.tool("search_debate_titles")
@log_tool_call
async def search_debate_titles(
    query: str = Field(..., description="Query used to search debate titles"),
    dateFrom: str | None = Field(None, description="Date from (YYYY-MM-DD)"),
    dateTo: str | None = Field(None, description="Date to (YYYY-MM-DD)"),
    house: Literal["Commons", "Lords"] | None = Field(None, description="House (Commons|Lords)"),
    maxResults: int = Field(50, description="Max results"),
) -> Any:
    """
    Search through the titles of debates for a given query, or by date range, and house.
    Only returns the debate ID, title, and date, not the content of the debate.
    Useful for finding relevant debates, but must be used in conjunction with search_contributions to get the full text of the debate.

    Either query or date range (or both) must be provided. House is optional.
    If only dateFrom is provided, returns debates from that date onwards.
    If only dateTo is provided, returns debates up to and including that date.

    Returns a list of debate details (ID, title, date) ranked by relevancy.

    Common use case for this function:
    - Provide a query to search for debates on a specific topic for all time
    - Provide the date for dateFrom and dateTo to search for all debates on a specific date
    - Provide a query and date range to search for debates on a specific topic in a specific date range

    Args:
        query: Text to search for in debate titles (optional if date range is provided)
        dateFrom: Start date in format 'YYYY-MM-DD' (optional if query is provided)
        dateTo: End date in format 'YYYY-MM-DD' (optional if query is provided)
        house: Filter by house (e.g., 'Commons', 'Lords'), optional
        maxResults: Maximum number of results to return (default 50)

    Returns:
        List of debate details dictionaries
    """
    ctx = mcp_server.get_context()
    qdrant_query_handler: QdrantQueryHandler = ctx.request_context.lifespan_context["qdrant_query_handler"]
    result = await qdrant_query_handler.search_debate_titles(
        query=query,
        date_from=dateFrom,
        date_to=dateTo,
        house=house,
        max_results=maxResults,
    )

    if not result:
        return "No results found"

    return result


@mcp_server.tool("find_relevant_contributors")
@log_tool_call
async def find_relevant_contributors(
    query: str = Field(..., description="Query used to search for relevant contributors"),
    num_contributors: int = Field(10, description="Number of contributors to return"),
    num_contributions: int = Field(10, description="Number of contributions to return"),
    dateFrom: str | None = Field(None, description="Date from (YYYY-MM-DD)"),
    dateTo: str | None = Field(None, description="Date to (YYYY-MM-DD)"),
    house: Literal["Commons", "Lords"] | None = Field(None, description="House (Commons|Lords)"),
) -> Any:
    """
    Find the most relevant parliamentary contributors and their contributions for a given query.

    Returns a list of contributor groups, each containing the member's contributions.

    Common use cases:
    - Provide a query to search for relevant contributors and their contributions for a specific topic
    - Provide a query, date range, and house to search for relevant contributors and their contributions for a specific topic in a specific date range and house
    """
    ctx = mcp_server.get_context()
    qdrant_query_handler: QdrantQueryHandler = ctx.request_context.lifespan_context["qdrant_query_handler"]
    result = await qdrant_query_handler.find_relevant_contributors(
        query=query,
        num_contributors=num_contributors,
        num_contributions=num_contributions,
        date_from=dateFrom,
        date_to=dateTo,
        house=house,
    )

    if not result:
        return "No results found"

    return result


# Hansard endpoints
@mcp_server.tool("search_contributions")
@log_tool_call
async def search_contributions(
    query: str | None = Field(
        None,
        description="""Searches within the actual spoken words/text of parliamentary contributions.
        Use this to find specific phrases, words, or topics that were mentioned during debates.
        For example, 'climate change' would find any time a member actually said something related to climate change.""",
    ),
    memberId: int | None = Field(None, description="Member ID, used to filter by a specific member"),
    dateFrom: str | None = Field(None, description="Date from (YYYY-MM-DD)"),
    dateTo: str | None = Field(None, description="Date to (YYYY-MM-DD)"),
    debateId: str | None = Field(None, description="Debate ID (Also called)"),
    house: Literal["Commons", "Lords"] | None = Field(None, description="House (Commons|Lords)"),
    maxResults: int = Field(50, description="Max results"),
) -> Any:
    """
    Search Hansard parliamentary records for contributions using searching within the actual spoken words
    A contribution is something a member said in the houses of parliament during a debate.

    Common use cases:
    - Search what was actually said: Use query="climate change" to find mentions in speeches
    - Member-specific search: Add memberId to find contributions by a specific member
    - Search by topic for a given memberId to find contributions on a specific topic by a specific member
    - Time-bounded search: Add dateFrom/dateTo to search within a specific period

    For the best results, you probably want to use a large maxResults.

    Returns:
        List of Hansard details dictionaries. Each one is a single contribution made during
        a debate in either the House of Commons or the House of Lords.
        Each dictionary contains:
            - contribution_id: ID of the contribution
            - text: Text of the contribution
            - attributed_to: Member who made the contribution
            - date: Date of the contribution
            - house: House of the contribution
            - member_id: ID of the member who made the contribution
    """
    ctx = mcp_server.get_context()
    qdrant_query_handler: QdrantQueryHandler = ctx.request_context.lifespan_context["qdrant_query_handler"]
    result = await qdrant_query_handler.search_hansard_contributions(
        query=query,
        memberId=memberId,
        dateFrom=dateFrom,
        dateTo=dateTo,
        debateId=debateId,
        house=house,
        maxResults=maxResults,
    )

    if not result:
        return "No results found"

    return result
