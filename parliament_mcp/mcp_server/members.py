import asyncio
import logging
import re
from typing import Any, Literal

from mcp.server.fastmcp.server import FastMCP
from pydantic import Field

from .utils import (
    clean_posts_list,
    extract_party_info,
    gather_sections,
    log_tool_call,
    request_committees_api,
    request_members_api,
    sanitize_params,
)

logger = logging.getLogger(__name__)


HTML_TAG_CLEANER = re.compile("<.*?>")


def remove_tags(text):
    return re.sub(HTML_TAG_CLEANER, "", text)


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


@log_tool_call
async def search_members(
    Name: str | None = Field(None, description="Member name"),
    PartyId: int | None = Field(None, description="Party ID"),
    House: Literal["Commons", "Lords"] | None = Field(None, description="House (Commons or Lords)"),
    member_since: str | None = Field(None, description="Was a member on or after date (YYYY-MM-DD)"),
    member_until: str | None = Field(None, description="Was a member on or before date (YYYY-MM-DD)"),
    Location: str | None = Field(
        None,
        description="Search by location name (e.g. 'Manchester' or 'Stratford') or by full or partial postcode (e.g. 'E20, PH41, SW1A 0AA')",
    ),
    IsCurrentMember: bool = Field(True, description="Whether the member is currently a member"),
    skip: int = Field(0, description="Number of results to skip"),
    take: int = Field(5, description="Number of results to take (Max 25), default 5"),
) -> Any:
    """
    Search for members of the Commons or Lords by name, post title, or other filters. It is recommended to take at least 5 results.

    Note: Multiple members may have the same name. The user may need to provide additional filters to narrow down the results.

    Returns a list of member details dictionaries. Each dictionary contains details such as:
        - id: ID of the member
        - name: Name of the member
        - latestHouseMembership.membershipFrom: Constituency of the member
        - party: Party of the member
        - house: House of the member (Commons or Lords)
        - membershipStartDate: Membership started since (YYYY-MM-DD)
        - membershipEndDate: Membership ended since (YYYY-MM-DD)
        - latestParty: Latest party of the member
    """
    params = sanitize_params(**locals())
    members = await request_members_api("/api/Members/Search", params)

    # Synopsis is decorative enrichment, so a failed one shouldn't sink the search.
    synopses = await asyncio.gather(
        *(request_members_api(f"/api/Members/{member['id']}/Synopsis") for member in members),
        return_exceptions=True,
    )
    for member, synopsis in zip(members, synopses, strict=True):
        if isinstance(synopsis, BaseException):
            logger.warning("Synopsis for member %s failed: %s", member["id"], synopsis)
            member["synopsis"] = ""
        else:
            member["synopsis"] = remove_tags(synopsis)

    return members


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
    include_committee_membership: bool = Field(
        False, description="Include all committees that the member has served in"
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

    # The member record anchors the result (and provides the house for the voting
    # lookup), so fetch it up front and let a genuine failure surface.
    member = await request_members_api(f"/api/Members/{member_id}")

    async def get_member_committees():
        result = await request_committees_api("/api/Members", params={"Members": [member_id]})
        return result[0]["committees"]

    sections = {}
    if include_synopsis:
        sections["synopsis"] = request_members_api(f"/api/Members/{member_id}/Synopsis")
    if include_biography:
        sections["biography"] = request_members_api(f"/api/Members/{member_id}/Biography")
    if include_contact:
        sections["contact"] = request_members_api(f"/api/Members/{member_id}/Contact", remove_null_values=True)
    if include_registered_interests:
        sections["registered_interests"] = request_members_api(f"/api/Members/{member_id}/RegisteredInterests")
    if include_committee_membership:
        sections["committee_membership"] = get_member_committees()
    if include_voting_record:
        member_house = member["latestHouseMembership"]["house"]
        sections["voting"] = request_members_api(f"/api/Members/{member_id}/Voting", params={"house": member_house})

    return {"member": member, **await gather_sections(sections)}


@log_tool_call
async def get_state_of_the_parties(
    house: Literal["Commons", "Lords"] = Field(..., description="Commons|Lords"),
    forDate: str = Field(..., description="YYYY-MM-DD"),
) -> Any:
    """Get state of the parties for a house on a specific date"""
    return await request_members_api(f"/api/Parties/StateOfTheParties/{house}/{forDate}")


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
        departments = await get_departments()
        department_posts_list = await asyncio.gather(
            *(
                request_members_api(f"/api/Posts/{post_type}", params={"departmentId": department["id"]})
                for department in departments
            ),
            return_exceptions=True,
        )

        posts = []
        for department, department_posts in zip(departments, department_posts_list, strict=True):
            if isinstance(department_posts, BaseException):
                logger.warning("Posts for department '%s' failed: %s", department["name"], department_posts)
                continue
            if department_posts:
                party_info = party_info or extract_party_info(department_posts)
                posts.append(
                    {
                        "department": department["name"],
                        "department_id": department["id"],
                        "posts": clean_posts_list(department_posts),
                    }
                )
    else:
        posts = await request_members_api(f"/api/Posts/{post_type}")

        party_info = extract_party_info(posts)

        posts = clean_posts_list(posts)

    return {"posts": posts, "party_info": party_info}


# Reference endpoints
@log_tool_call
async def get_departments() -> Any:
    """Get departments"""
    results = await request_members_api("/api/Reference/Departments")
    # Drop malformed entries so downstream department["id"] lookups stay safe.
    results = [department for department in results if isinstance(department, dict) and "id" in department]
    # Leader of HM Official Opposition is a special case not returned by the departments API
    results.append({"id": 107, "name": "Leader of HM Official Opposition"})
    return results


def register_members_tools(mcp_server: FastMCP):
    mcp_server.tool("get_election_results")(get_election_results)
    mcp_server.tool("search_members")(search_members)
    mcp_server.tool("get_detailed_member_information")(get_detailed_member_information)
    mcp_server.tool("get_state_of_the_parties")(get_state_of_the_parties)
    mcp_server.tool("list_ministerial_roles")(list_ministerial_roles)
    mcp_server.tool("get_departments")(get_departments)
