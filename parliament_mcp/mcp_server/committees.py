import asyncio
import base64
import logging
from datetime import UTC, datetime
from typing import Literal

from markdownify import markdownify as md
from mcp.server.fastmcp.server import FastMCP

from parliament_mcp.qdrant_data_loaders import cached_limited_get

from .utils import COMMITTEES_API_BASE_URL, log_tool_call, request_committees_api

logger = logging.getLogger(__name__)

MAX_COMMITTEES_PER_REQUEST = 256


def clean_committee_item(committee_item: dict):
    """
    Remove the following keys:
    - nameHistory
    - websiteLegacyRedirectEnabled
    - websiteLegacyUrl
    - showOnWebsite

    Replace the `committeeTypes` key with a list of the `name` values
    Replace the `category` key with the `name` value
    """
    # print(committee_item)
    keys_to_remove = ["nameHistory", "websiteLegacyRedirectEnabled", "websiteLegacyUrl", "showOnWebsite"]
    for key in keys_to_remove:
        committee_item.pop(key, None)
    committee_item["committeeTypes"] = [committee_type["name"] for committee_type in committee_item["committeeTypes"]]
    if "category" in committee_item:
        committee_item["category"] = committee_item["category"]["name"]
    for sub_item in committee_item.get("subCommittees", []):
        clean_committee_item(sub_item)
    if "parentCommittee" in committee_item:
        clean_committee_item(committee_item["parentCommittee"])
    return committee_item


async def get_committee_business(committee_id: int):
    result = await request_committees_api(
        "/api/CommitteeBusiness", params={"CommitteeId": committee_id, "Status": "Open"}
    )

    inquiries = []
    other_business = []
    for item in result["items"]:
        open_date = item["openDate"].split("T")[0]

        if item["type"]["name"] == "Inquiry":
            inquiries.append({"id": item["id"], "title": item["title"], "openDate": open_date})
        else:
            other_business.append({"id": item["id"], "title": item["title"], "openDate": open_date})

    return {"inquiries": inquiries, "other_business": other_business}


async def get_committee_events(committee_id: int, upcoming_only: bool = True):
    params = {"StartDateFrom": datetime.now(tz=UTC).date().isoformat()} if upcoming_only else {}
    reponse = await request_committees_api(f"/api/Committees/{committee_id}/Events", params=params)
    result = []
    reponse = reponse["items"]
    for item in reponse:
        if len(item["committeeBusinesses"]) == 0:
            continue

        result.append(
            {
                "id": item["id"],
                "type": item["eventType"]["name"],
                "date": item["startDate"].split("T")[0],
                "committeeBusinesses": [
                    {
                        "id": business["id"],
                        "title": business["title"],
                        "type": business["type"]["name"],
                    }
                    for business in item["committeeBusinesses"]
                ],
            }
        )
    return result


async def get_committee_members(committee_id: int):
    response = await request_committees_api(
        f"/api/Committees/{committee_id}/Members", params={"MembershipStatus": "All"}
    )

    def format_role(role):
        role_name = role["role"]["name"]
        start_date = role["startDate"].split("T")[0]
        end_date = end_date.split("T")[0] if (end_date := role.get("endDate")) else "present"
        return f"{role_name} ({start_date} - {end_date})"

    members = []
    for member in response["items"]:
        members.append(
            {
                "isLayMember": member.get("isLayMember", False),
                "member_id": member.get("memberInfo", {}).get("mnisId", None),
                "name": member["name"],
                "constituency": member.get("memberInfo", {}).get("memberFrom", ""),
                "party": member.get("memberInfo", {}).get("party", ""),
                "roles": [format_role(role) for role in member["roles"]],
                "isCurrent": member.get("memberInfo", {}).get("isCurrent", False),
            }
        )
    return members


def format_witness(witness: dict):
    if witness["submitterType"] == "Organisation":
        return witness["organisations"][0]["name"]
    return witness["name"]


async def get_committee_oral_evidence(committee_id: int):
    response = await request_committees_api("/api/OralEvidence", params={"CommitteeId": committee_id})

    oral_evidence = []
    for item in response["items"]:
        date = item.get("meetingDate") or item.get("activityStartDate") or item.get("publicationDate")
        date = date.split("T")[0]
        oral_evidence.append(
            {
                "id": item["id"],
                "date": date,
                "witnesses": [format_witness(witness) for witness in item["witnesses"]],
                "businesses": [
                    {
                        "id": business["id"],
                        "title": business["title"],
                        "type": business["type"]["name"],
                    }
                    for business in item["committeeBusinesses"]
                ],
            }
        )
    return oral_evidence


async def get_committee_written_evidence(committee_id: int):
    response = await request_committees_api("/api/WrittenEvidence", params={"CommitteeId": committee_id})

    written_evidence = []
    for item in response["items"]:
        written_evidence.append(
            {
                "id": item["id"],
                "publicationDate": item["publicationDate"].split("T")[0],
                "witnesses": [format_witness(witness) for witness in item["witnesses"]],
                "business": {
                    "id": item["committeeBusiness"]["id"],
                    "title": item["committeeBusiness"]["title"],
                    "type": item["committeeBusiness"]["type"]["name"],
                },
            }
        )
    return written_evidence


async def get_committee_publications(committee_id: int):
    response = await request_committees_api("/api/Publications", params={"CommitteeId": committee_id})

    publications = []
    for item in response["items"]:
        publications.append(
            {
                "id": item["id"],
                "description": item["description"],
                "type": item["type"]["name"],
                "type_description": item["type"]["description"],
                "publicationStartDate": item["publicationStartDate"].split("T")[0],
                "document_ids": [document["documentId"] for document in item["documents"]],
                "businesses": [
                    {
                        "id": business["id"],
                        "title": business["title"],
                        "type": business["type"]["name"],
                    }
                    for business in item["businesses"]
                ],
            }
        )
    return publications


@log_tool_call
async def list_all_committees(
    committee_status: Literal["Current", "Former", "All"] = "Current",
    house: Literal["Commons", "Lords", "Joint"] = "Commons",
):
    """
    List all committees

    Args:
        committee_status: The status of the committee (Current, Former, All)
        house: The house of the committee (Commons, Lords, Joint)

    Returns:
        A list of committees
        Each committee is a dictionary with the following keys:
        - id: The ID of the committee
        - name: The name of the committee
        - purpose: The purpose of the committee
        - category: The category of the committee
        - subCommittees: A list of sub-committees
    """
    result = await request_committees_api(
        "/api/Committees", params={"CommitteeStatus": committee_status, "House": house, "Take": 256}
    )

    if result["totalResults"] == MAX_COMMITTEES_PER_REQUEST:
        logger.warning("There are more committees to fetch")

    # filter out committees that have a `parentCommittee`. Only keep the top level committees.
    committees = [item for item in result["items"] if item.get("parentCommittee") is None]

    return [clean_committee_item(item) for item in committees]


async def get_basic_committee_info(committee_id: int):
    response = await request_committees_api(f"/api/Committees/{committee_id}")
    return {
        "id": response["id"],
        "name": response["name"],
        "purpose": response["purpose"],
        "category": response["category"]["name"],
        "house": response["house"],
        "subCommittees": [clean_committee_item(sub_committee) for sub_committee in response["subCommittees"]],
    }


@log_tool_call
async def get_committee_details(committee_id: int):
    """
    Get extensive and detailed information about a particular committee

    Args:
        committee_id: The ID of the committee

    Returns:
        A dictionary with the following keys:
        - basic_committee_info: Some summary information about the committee
        - members: A list of members that have served on the committee
        - publications: A list of publications that have been produced by the committee
        - oral_evidence: A list of oral evidence sessions that have been held by the committee
        - written_evidence: A list of written evidence that has been submitted to the committee
        - committee_business: A list of business that has been conducted by the committee
        - upcoming_events: A list of upcoming events that are scheduled for the committee
    """
    tasks = {}
    async with asyncio.TaskGroup() as tg:
        tasks["basic_committee_info"] = tg.create_task(get_basic_committee_info(committee_id))
        tasks["members"] = tg.create_task(get_committee_members(committee_id))
        tasks["publications"] = tg.create_task(get_committee_publications(committee_id))
        tasks["oral_evidence"] = tg.create_task(get_committee_oral_evidence(committee_id))
        tasks["written_evidence"] = tg.create_task(get_committee_written_evidence(committee_id))
        tasks["committee_business"] = tg.create_task(get_committee_business(committee_id))
        tasks["upcoming_events"] = tg.create_task(get_committee_events(committee_id, upcoming_only=True))

    return {
        "basic_committee_info": tasks["basic_committee_info"].result(),
        "members": tasks["members"].result(),
        "oral_evidence": tasks["oral_evidence"].result(),
        "written_evidence": tasks["written_evidence"].result(),
        "publications": tasks["publications"].result(),
        "committee_business": tasks["committee_business"].result(),
        "upcoming_events": tasks["upcoming_events"].result(),
    }


@log_tool_call
async def get_committee_document(document_id: int, document_type: Literal["oral_evidence", "written_evidence"]):
    """
    Get a document from a committee

    Use the oral evidence ID or the written evidence ID to get the document.

    Examples:
    - get_committee_document(document_id=123, document_type="oral_evidence")
    - get_committee_document(document_id=123, document_type="written_evidence")

    Args:
        document_id: The ID of the document
        document_type: The type of document (oral_evidence, written_evidence)
    """
    if document_type == "oral_evidence":
        response = await cached_limited_get(
            f"{COMMITTEES_API_BASE_URL}/api/OralEvidence/{document_id}/Document/Html",
            headers={"accept": "application/json"},
        )
    else:
        response = await cached_limited_get(
            f"{COMMITTEES_API_BASE_URL}/api/WrittenEvidence/{document_id}/Document/Html",
            headers={"accept": "application/json"},
        )

    response.raise_for_status()
    data = response.json()["data"]
    return md(base64.b64decode(data).decode("utf-8"), strip=["img"])


def register_committee_tools(mcp_server: FastMCP):
    """Register all committee-related tools with the MCP server"""

    mcp_server.add_tool(list_all_committees, "list_all_committees")
    mcp_server.add_tool(get_committee_details, "get_committee_details")
    mcp_server.add_tool(get_committee_document, "get_committee_document")
