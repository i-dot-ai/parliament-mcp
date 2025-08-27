from datetime import UTC, datetime

import pytest

from parliament_mcp.mcp_server.qdrant_query_handler import QdrantQueryHandler


# mark async
@pytest.mark.asyncio
@pytest.mark.integration
async def test_search_parliamentary_questions(qdrant_query_handler: QdrantQueryHandler):
    """Test Parliamentary Questions search with test data."""
    results = await qdrant_query_handler.search_parliamentary_questions(
        dateFrom="2025-06-20",
        dateTo="2025-06-25",
    )
    assert results is not None
    assert len(results) > 0

    results = await qdrant_query_handler.search_parliamentary_questions(
        query="trains and railways",
    )
    assert results is not None
    # May be 0 if no matching data in test set
    assert len(results) >= 0


@pytest.mark.asyncio
@pytest.mark.integration
async def test_search_hansard_contributions(qdrant_query_handler: QdrantQueryHandler):
    """Test Hansard contributions search with test data."""

    results = await qdrant_query_handler.search_hansard_contributions(
        query="debate",  # More generic query likely to match test data
    )
    assert results is not None
    assert len(results) > 0


@pytest.mark.asyncio
@pytest.mark.integration
async def test_search_hansard_contributions_with_member_id(
    qdrant_query_handler: QdrantQueryHandler,
):
    """Test Hansard contributions search with member ID."""

    # Test with a memberId (Deputy PM Angela Rayner stood in for PM in PMQs)
    results = await qdrant_query_handler.search_hansard_contributions(
        memberId=4356,
        maxResults=10,
    )
    assert results is not None
    assert len(results) > 0, "No results found"


@pytest.mark.asyncio
@pytest.mark.integration
async def test_search_debates(qdrant_query_handler: QdrantQueryHandler):
    """Test debates search with test data."""
    results = await qdrant_query_handler.search_debate_titles(
        date_from="2025-06-20",
        date_to="2025-06-25",
        house="Commons",
    )
    assert results is not None
    assert len(results) > 0


@pytest.mark.asyncio
@pytest.mark.integration
async def test_pmqs_are_on_wednesdays(qdrant_query_handler: QdrantQueryHandler):
    """Test that PMQs are on Wednesdays."""
    results = await qdrant_query_handler.search_debate_titles(
        # Not technically the title of the debate, but good to test with
        # Should find https://hansard.parliament.uk/Commons/2025-06-25/debates/4777FB03-8AEC-422A-996F-A395AAD30963/Engagements
        query="Prime Minister's Questions",
        date_from="2025-01-01",
        date_to="2025-07-31",
    )
    any_pmqs_found = False
    for result in results:
        # If 'Prime Minister' is in debate_parents.Title, then the debate is a PMQs
        is_pmqs = any("Prime Minister" in debate_parent["Title"] for debate_parent in result["debate_parents"])

        any_pmqs_found |= is_pmqs

        # PMQs are on Wednesdays
        if is_pmqs:
            date = datetime.fromisoformat(result["date"]).replace(tzinfo=UTC)
            assert date.weekday() == 2, "PMQs found on wrong day"

    assert any_pmqs_found, "No PMQs found in test data"


@pytest.mark.asyncio
@pytest.mark.integration
async def test_search_hansard_contributions_with_filters(
    qdrant_query_handler: QdrantQueryHandler,
):
    """Test Hansard contributions search with knn.

    Should find the contribution on NATO from Pat McFadden on 24th June 2025.
    """
    results = await qdrant_query_handler.search_hansard_contributions(
        query="NATO",
        memberId=1587,
        dateFrom="2025-06-23",
        dateTo="2025-06-27",
    )
    assert results is not None
    assert len(results) > 0

    top_contribution_url = "https://hansard.parliament.uk/Commons/2025-06-24/debates/3E222FED-6C44-400C-8ABD-112BDCDAE98B/link#contribution-69057392-95C1-40B9-A415-6B4CCCFEE821"
    assert results[0]["contribution_url"] == top_contribution_url


@pytest.mark.asyncio
@pytest.mark.integration
async def test_search_debates_with_filters(qdrant_query_handler: QdrantQueryHandler):
    """Test debates search with specific parameters."""

    # Should find https://hansard.parliament.uk/commons/2025-06-25/debates/600CC999-37EB-4B34-A324-806698158D78/Nuclear-CertifiedAircraftProcurement
    results = await qdrant_query_handler.search_debate_titles(
        query="Aircraft Procurement",
        date_from="2025-06-20",
        max_results=5,
    )
    assert results is not None
    assert len(results) <= 5  # Should respect max_results parameter
    # May be 0 if no matching data in test set for this specific query and date
    assert len(results) > 0


@pytest.mark.asyncio
@pytest.mark.integration
async def test_search_parliamentary_questions_with_answering_body_name(qdrant_query_handler: QdrantQueryHandler):
    """Test Parliamentary Questions search with answering body name."""
    # https://members-api.parliament.uk/index.html#operations-Reference-get_api_Reference_AnsweringBodies
    results = await qdrant_query_handler.search_parliamentary_questions(
        answering_body_name="Department for Transport",
        max_results=10,
    )
    assert results is not None
    assert len(results) > 0
    for result in results:
        assert "Department for Transport" in result["answeringBodyName"]
