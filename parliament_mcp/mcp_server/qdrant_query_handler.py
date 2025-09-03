from datetime import datetime
from typing import Any, Literal

from fastembed import SparseTextEmbedding
from openai import AsyncAzureOpenAI
from qdrant_client import AsyncQdrantClient, models
from qdrant_client.models import DatetimeRange, FieldCondition, Filter, MatchValue

from parliament_mcp.openai_helpers import embed_single
from parliament_mcp.settings import ParliamentMCPSettings

MINIMUM_DEBATE_HITS = 2


def parse_date(date_str: str | None) -> str | None:
    if not date_str:
        return None
    try:
        return datetime.fromisoformat(date_str).date().isoformat()
    except (ValueError, TypeError):
        return None


def build_date_range_filter(
    date_from: str | None, date_to: str | None, field: str = "SittingDate"
) -> FieldCondition | None:
    """Build a date range filter for Qdrant queries using DatetimeRange."""
    if not date_from and not date_to:
        return None

    return FieldCondition(
        key=field,
        range=DatetimeRange(
            gte=datetime.fromisoformat(date_from).date() if date_from else None,
            lte=datetime.fromisoformat(date_to).date() if date_to else None,
        ),
    )


def build_match_filter(field: str, value: Any) -> FieldCondition | None:
    """Build a match filter for Qdrant queries."""
    if value is None:
        return None
    return FieldCondition(key=field, match=MatchValue(value=value))


def build_filters(conditions: list[FieldCondition | None]) -> Filter | None:
    """Build a Qdrant filter from a list of conditions."""
    valid_conditions = [c for c in conditions if c is not None]
    if not valid_conditions:
        return None

    return Filter(must=valid_conditions)


class QdrantQueryHandler:
    def __init__(
        self, qdrant_client: AsyncQdrantClient, openai_client: AsyncAzureOpenAI, settings: ParliamentMCPSettings
    ):
        self.qdrant_client = qdrant_client
        self.openai_client = openai_client
        self.sparse_text_embedding = SparseTextEmbedding(model_name=settings.SPARSE_TEXT_EMBEDDING_MODEL)
        self.settings = settings

    async def embed_query_dense(self, query: str) -> list[float]:
        """Embed a query using the dense text embedding model."""
        return await embed_single(
            self.openai_client,
            query,
            self.settings.AZURE_OPENAI_EMBEDDING_MODEL,
            self.settings.EMBEDDING_DIMENSIONS,
        )

    def embed_query_sparse(self, query: str) -> models.SparseVector:
        """Embed a query using the sparse text embedding model."""
        embedding = next(self.sparse_text_embedding.embed(query))
        return models.SparseVector(indices=embedding.indices, values=embedding.values)

    async def search_debate_titles(
        self,
        query: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        house: str | None = None,
        max_results: int = 100,
    ) -> list[dict]:
        """
        Search debate titles with optional filters.

        Args:
            query: Text to search for in debate titles (optional if date range is provided)
            date_from: Start date in format 'YYYY-MM-DD' (optional if query is provided)
            date_to: End date in format 'YYYY-MM-DD' (optional if query is provided)
            house: Filter by house (e.g., 'Commons', 'Lords'), optional
            max_results: Maximum number of results to return (default 100)

        Returns:
            List of debate details dictionaries

        Raises:
            ValueError: If neither query nor date range is provided
        """
        # Validate that at least one of query or date range is provided
        if not query and not date_from and not date_to:
            message = "At least one of 'query', 'date_from', or 'date_to' must be provided"
            raise ValueError(message)

        # Build filters
        filter_conditions = [
            build_date_range_filter(date_from, date_to),
            build_match_filter("House", house),
        ]

        if query:
            filter_conditions.append(FieldCondition(key="debate_parents[].Title", match=models.MatchText(text=query)))

        query_filter = build_filters(filter_conditions)

        query_response = await self.qdrant_client.query_points_groups(
            collection_name=self.settings.HANSARD_CONTRIBUTIONS_COLLECTION,
            query_filter=query_filter,
            limit=max_results,
            with_payload=True,
            group_by="DebateSectionExtId",
            group_size=MINIMUM_DEBATE_HITS,
        )

        results = []
        for group in query_response.groups:
            first_hit = group.hits[0].payload
            # If there are less than 2 hits, the debate is not relevant
            if len(group.hits) < MINIMUM_DEBATE_HITS:
                continue

            debate = {
                "debate_id": first_hit.get("DebateSectionExtId"),
                "title": first_hit.get("DebateSection"),
                "date": first_hit.get("SittingDate"),
                "house": first_hit.get("House"),
                "debate_parents": first_hit.get("debate_parents", []),
            }

            results.append(debate)

        return results

    async def search_hansard_contributions(
        self,
        query: str | None = None,
        member_id: int | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        debate_id: str | None = None,
        house: Literal["Commons", "Lords"] | None = None,
        max_results: int = 100,
        min_score: float = 0,
    ) -> list[dict]:
        """
        Search Hansard contributions using Qdrant vector search.

        Args:
            query: Text to search for in contributions (optional)
            member_id: Member ID (optional)
            date_from: Start date in format 'YYYY-MM-DD' (optional)
            date_to: End date in format 'YYYY-MM-DD' (optional)
            debate_id: Debate ID (optional)
            house: House (Commons|Lords) (optional)
            max_results: Maximum number of results to return (default 100)
            min_score: Minimum relevance score (default 0)

        Returns:
            List of Hansard contribution details dictionaries

        Raises:
            ValueError: If no search parameters are provided
        """
        # Fail if none of the parameters are provided
        if not query and not member_id and not date_from and not date_to and not debate_id and not house:
            msg = (
                "At least one of 'query', 'member_id', 'date_from', 'date_to', 'debate_id' or 'house' must be provided"
            )
            raise ValueError(msg)

        # Build filters
        filter_conditions = [
            build_match_filter("MemberId", member_id),
            build_match_filter("DebateSectionExtId", debate_id),
            build_match_filter("House", house),
            build_date_range_filter(date_from, date_to),
        ]

        query_filter = build_filters(filter_conditions)

        if query:
            # Generate embedding for search query
            dense_query_vector = await self.embed_query_dense(query)
            sparse_query_vector = self.embed_query_sparse(query)

            # Perform vector search
            query_response = await self.qdrant_client.query_points(
                collection_name=self.settings.HANSARD_CONTRIBUTIONS_COLLECTION,
                prefetch=[
                    models.Prefetch(
                        query=dense_query_vector,
                        using="text_dense",
                        limit=max_results,
                        filter=query_filter,
                    ),
                    models.Prefetch(
                        query=sparse_query_vector,
                        using="text_sparse",
                        limit=max_results,
                        filter=query_filter,
                    ),
                ],
                query=models.FusionQuery(
                    fusion=models.Fusion.RRF,
                ),
                limit=max_results,
                score_threshold=min_score,
                with_payload=True,
            )
        else:
            # If no query, use scroll to get results with filters only
            query_response = await self.qdrant_client.query_points(
                collection_name=self.settings.HANSARD_CONTRIBUTIONS_COLLECTION,
                query_filter=query_filter,
                limit=max_results,
                with_payload=True,
            )

        results = []
        for result in query_response.points:
            payload = result.payload
            results.append(
                {
                    "text": payload.get("text", ""),
                    "date": payload.get("SittingDate"),
                    "house": payload.get("House"),
                    "member_id": payload.get("MemberId"),
                    "member_name": payload.get("MemberName"),
                    "relevance_score": result.score if hasattr(result, "score") else 1.0,
                    "debate_title": payload.get("DebateSection", ""),
                    "debate_url": payload.get("debate_url", ""),
                    "contribution_url": payload.get("contribution_url", ""),
                    "order_in_debate": payload.get("OrderInDebateSection"),
                    "debate_parents": payload.get("debate_parents", []),
                }
            )

        # Sort by relevance score if we have a query, otherwise by date and order
        if query:
            results.sort(key=lambda x: x["relevance_score"], reverse=True)
        else:
            results.sort(
                key=lambda x: (
                    x.get("date", ""),
                    x.get("order_in_debate", 0),
                )
            )

        return results

    async def find_relevant_contributors(
        self,
        query: str,
        num_contributors: int = 10,
        num_contributions: int = 10,
        date_from: str | None = None,
        date_to: str | None = None,
        house: Literal["Commons", "Lords"] | None = None,
    ) -> list[dict]:
        """
        Find the most relevant parliamentary contributors and their contributions.

        Groups Hansard contributions by member ID and returns the top contributors
        with their most relevant contributions for the given search query.

        Args:
            query: Text to search for in contributions
            num_contributors: Number of top contributors to return (default 10)
            num_contributions: Number of top contributions per contributor (default 10)
            date_from: Start date filter in 'YYYY-MM-DD' format (optional)
            date_to: End date filter in 'YYYY-MM-DD' format (optional)
            house: Filter by house - "Commons" or "Lords" (optional)

        Returns:
            List of contributor groups, each containing the member's contributions
        """
        # Fail if none of the parameters are provided
        if not query:
            msg = "A query must be provided"
            raise ValueError(msg)

        # Build filters
        query_filter = build_filters(
            [
                build_match_filter("House", house),
                build_date_range_filter(date_from, date_to),
            ]
        )

        # Generate embedding for search query
        dense_query_vector = await self.embed_query_dense(query)
        sparse_query_vector = self.embed_query_sparse(query)

        # Perform vector search
        query_response = await self.qdrant_client.query_points_groups(
            collection_name=self.settings.HANSARD_CONTRIBUTIONS_COLLECTION,
            prefetch=[
                models.Prefetch(
                    query=dense_query_vector,
                    using="text_dense",
                    filter=query_filter,
                ),
                models.Prefetch(
                    query=sparse_query_vector,
                    using="text_sparse",
                    filter=query_filter,
                ),
            ],
            query=models.FusionQuery(
                fusion=models.Fusion.RRF,
            ),
            limit=num_contributors,
            score_threshold=0,
            with_payload=True,
            group_by="MemberId",
            group_size=num_contributions,
        )

        results = []
        for group in query_response.groups:
            group_results = []
            for hit in group.hits:
                payload = hit.payload
                group_results.append(
                    {
                        "text": payload.get("text", ""),
                        "date": payload.get("SittingDate"),
                        "house": payload.get("House"),
                        "member_id": payload.get("MemberId"),
                        "member_name": payload.get("MemberName"),
                        "relevance_score": hit.score if hasattr(hit, "score") else 1.0,
                        "debate_title": payload.get("DebateSection", ""),
                        "debate_url": payload.get("debate_url", ""),
                        "contribution_url": payload.get("contribution_url", ""),
                        "order_in_debate": payload.get("OrderInDebateSection"),
                        "debate_parents": payload.get("debate_parents", []),
                    }
                )

            results.append(group_results)

        return results

    async def search_parliamentary_questions(
        self,
        query: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        party: str | None = None,
        asking_member_id: int | None = None,
        answering_body_name: str | None = None,
        min_score: float = 0,
        max_results: int = 100,
    ) -> list[dict]:
        """
        Search Parliamentary Questions using Qdrant vector search.

        Args:
            query: Text to search for in parliamentary questions
            date_from: Start date in format 'YYYY-MM-DD' (optional)
            date_to: End date in format 'YYYY-MM-DD' (optional)
            party: Filter by party (optional)
            asking_member_id: Filter by member id (optional)
            answering_body_name: Filter by answering body name (optional)
            min_score: Minimum relevance score (default 0)
            max_results: Maximum number of results to return (default 100)
        """
        # Build filters
        filter_conditions = [
            build_date_range_filter(date_from, date_to, "dateTabled"),
            build_match_filter("askingMember.party", party),
            build_match_filter("askingMember.id", asking_member_id),
        ]

        if answering_body_name:
            filter_conditions.append(
                FieldCondition(key="answeringBodyName", match=models.MatchText(text=answering_body_name))
            )

        query_filter = build_filters(filter_conditions)

        if query:
            # Generate embedding for search query
            dense_query_vector = await self.embed_query_dense(query)
            sparse_query_vector = self.embed_query_sparse(query)

            # Perform vector search
            query_response = await self.qdrant_client.query_points_groups(
                collection_name=self.settings.PARLIAMENTARY_QUESTIONS_COLLECTION,
                prefetch=[
                    models.Prefetch(
                        query=dense_query_vector,
                        using="text_dense",
                        limit=max_results,
                        filter=query_filter,
                    ),
                    models.Prefetch(
                        query=sparse_query_vector,
                        using="text_sparse",
                        limit=max_results,
                        filter=query_filter,
                    ),
                ],
                query=models.FusionQuery(
                    fusion=models.Fusion.RRF,
                ),
                limit=max_results,
                score_threshold=min_score,
                with_payload=True,
                group_by="id",
                group_size=10,
            )
        else:
            # If no query, use scroll to get results with filters only
            query_response = await self.qdrant_client.query_points_groups(
                collection_name=self.settings.PARLIAMENTARY_QUESTIONS_COLLECTION,
                query_filter=query_filter,
                limit=max_results,
                with_payload=True,
                group_by="id",
                group_size=100,
            )

        results = []
        for group in query_response.groups:
            # For PQs, question and answer chunks are stored as separate chunks, so we have to piece them together

            payloads = [hit.payload for hit in group.hits]
            answer_chunks = [(hit["chunk_id"], hit["text"]) for hit in payloads if hit["chunk_type"] == "answer"]
            question_chunks = [(hit["chunk_id"], hit["text"]) for hit in payloads if hit["chunk_type"] == "question"]

            answer_text = "\n".join([text for _, text in sorted(answer_chunks)])
            question_text = "\n".join([text for _, text in sorted(question_chunks)])

            # use the latest created_at payload
            payload = max(payloads, key=lambda x: x.get("created_at"))

            uin = payload.get("uin")
            tabled_date = parse_date(payload.get("dateTabled"))

            results.append(
                {
                    "question_text": question_text,
                    "answer_text": answer_text,
                    "chunk_type": payload.get("chunk_type"),
                    "askingMember": payload.get("askingMember"),
                    "answeringMember": payload.get("answeringMember"),
                    "dateTabled": parse_date(payload.get("dateTabled")),
                    "dateAnswered": parse_date(payload.get("dateAnswered")),
                    "answeringBodyName": payload.get("answeringBodyName"),
                    "question_url": f"https://questions-statements.parliament.uk/written-questions/detail/{tabled_date}/{uin}",
                }
            )

        return results
