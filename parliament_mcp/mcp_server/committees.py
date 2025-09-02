import asyncio
import base64
import io
import logging
from datetime import UTC, datetime
from typing import Literal

from markdownify import markdownify as md
from markitdown import MarkItDown
from mcp.server.fastmcp.server import FastMCP

from parliament_mcp.qdrant_data_loaders import cached_limited_get

from .utils import COMMITTEES_API_BASE_URL, log_tool_call, request_committees_api

logger = logging.getLogger(__name__)

MAX_COMMITTEES_PER_REQUEST = 256

markitdown = MarkItDown()


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


async def get_publication_document(publication_id: int, document_id: int):
    response = await cached_limited_get(
        f"{COMMITTEES_API_BASE_URL}/api/Publications/{publication_id}/Document/{document_id}/OriginalFormat",
        headers={"accept": "application/json"},
    )
    response.raise_for_status()
    data = response.json()

    file_name_suffix = data["fileName"].split(".")[-1].lower()
    if file_name_suffix in {"docx", "pdf", "xlsx"}:
        binary_io = io.BytesIO(base64.b64decode(data["data"]))
        document = markitdown.convert(binary_io)
    elif file_name_suffix == "html":
        document = md(base64.b64decode(data["data"]).decode("utf-8"), strip=["img"])

    else:
        message = f"Unsupported document type: {file_name_suffix}"
        logger.error(message)
        raise ValueError(message) or (message)
    return {
        "id": document_id,
        "document": document,
        "file_name": data["fileName"],
        "file_name_suffix": file_name_suffix,
    }


@log_tool_call
async def get_committee_details(
    committee_id: int,
    include_members: bool = True,
    include_publications: bool = True,
    include_oral_evidence: bool = True,
    include_written_evidence: bool = True,
    include_business: bool = True,
    include_upcoming_events: bool = True,
):
    """
    Get information about a particular committee. All sections are included by default.

    Args:
        committee_id: The ID of the committee
        include_members: Include list of committee members (default: True)
        include_publications: Include committee publications (default: True)
        include_oral_evidence: Include oral evidence sessions (default: True)
        include_written_evidence: Include written evidence submissions (default: True)
        include_business: Include committee business/inquiries (default: True)
        include_upcoming_events: Include upcoming events (default: True)

    Returns:
        A dictionary with requested sections:
        - basic_committee_info: Always included - summary information about the committee
        - members: List of members (if requested)
        - publications: List of publications (if requested)
        - oral_evidence: List of oral evidence sessions (if requested)
        - written_evidence: List of written evidence (if requested)
        - committee_business: List of business/inquiries (if requested)
        - upcoming_events: List of upcoming events (if requested)
    """
    tasks = {}
    async with asyncio.TaskGroup() as tg:
        # Always include basic info
        tasks["basic_committee_info"] = tg.create_task(get_basic_committee_info(committee_id))
        # Conditionally include other sections
        if include_members:
            tasks["members"] = tg.create_task(get_committee_members(committee_id))
        if include_publications:
            tasks["publications"] = tg.create_task(get_committee_publications(committee_id))
        if include_oral_evidence:
            tasks["oral_evidence"] = tg.create_task(get_committee_oral_evidence(committee_id))
        if include_written_evidence:
            tasks["written_evidence"] = tg.create_task(get_committee_written_evidence(committee_id))
        if include_business:
            tasks["committee_business"] = tg.create_task(get_committee_business(committee_id))
        if include_upcoming_events:
            tasks["upcoming_events"] = tg.create_task(get_committee_events(committee_id, upcoming_only=True))

    # Build result with only requested sections
    result = {}
    for key, task in tasks.items():
        result[key] = task.result()
    return result


@log_tool_call
async def get_committee_document(
    document_type: Literal["oral_evidence", "written_evidence", "publication"],
    evidence_id: int | None = None,
    publication_id: int | None = None,
    document_ids: list[int] | None = None,
):
    """
    Get committee documents - evidence or publications

    For evidence documents (oral or written):
    - Provide document_type and evidence_id
    - Returns the evidence document as markdown

    For publication documents:
    - Provide document_type="publication", publication_id, and document_ids
    - Returns a list of publication documents

    Examples:
    - get_committee_document(document_type="oral_evidence", evidence_id=123)
    - get_committee_document(document_type="written_evidence", evidence_id=456)
    - get_committee_document(document_type="publication", publication_id=789, document_ids=[111, 222])

    Args:
        document_type: Type of document (oral_evidence, written_evidence, publication)
        evidence_id: ID for evidence documents (oral or written)
        publication_id: ID for publication documents
        document_ids: List of document IDs for publication documents
    """

    # Handle evidence documents
    if document_type in ["oral_evidence", "written_evidence"]:
        if evidence_id is None:
            message = f"evidence_id required for {document_type} documents"
            logger.error(message)
            raise ValueError(message)

        endpoint = "OralEvidence" if document_type == "oral_evidence" else "WrittenEvidence"
        response = await cached_limited_get(
            f"{COMMITTEES_API_BASE_URL}/api/{endpoint}/{evidence_id}/Document/Html",
            headers={"accept": "application/json"},
        )
        response.raise_for_status()
        data = response.json()["data"]
        return md(base64.b64decode(data).decode("utf-8"), strip=["img"])

    # Handle publication documents
    elif document_type == "publication":
        if publication_id is None or document_ids is None:
            message = "publication_id and document_ids required for publication documents"
            logger.error(message)
            raise ValueError(message)

        documents = []
        async with asyncio.TaskGroup() as tg:
            for document_id in document_ids:
                documents.append(tg.create_task(get_publication_document(publication_id, document_id)))

        return [document.result() for document in documents]
    else:
        message = f"Invalid document_type: {document_type}"
        logger.error(message)
        raise ValueError(message)


def register_committee_tools(mcp_server: FastMCP):
    """Register all committee-related tools with the MCP server"""

    mcp_server.add_tool(list_all_committees, "list_all_committees")
    mcp_server.add_tool(get_committee_details, "get_committee_details")
    mcp_server.add_tool(get_committee_document, "get_committee_document")
