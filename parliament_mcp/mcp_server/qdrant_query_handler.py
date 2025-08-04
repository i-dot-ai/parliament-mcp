from datetime import datetime
from typing import Any, Literal

from fastembed import SparseTextEmbedding
from openai import AsyncAzureOpenAI
from qdrant_client import AsyncQdrantClient, models
from qdrant_client.models import DatetimeRange, FieldCondition, Filter, MatchValue

from parliament_mcp.embedding_helpers import embed_single
from parliament_mcp.settings import ParliamentMCPSettings

MINIMUM_DEBATE_HITS = 2


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

    async def search_debates(
        self,
        query: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        house: str | None = None,
        max_results: int = 100,
    ) -> list[dict]:
        """
        Search debates using Qdrant vector search with optional filters.

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

        query_filter = build_filters(filter_conditions)

        if query:
            # Generate embeddings for search query
            dense_query_vector = await self.embed_query_dense(query)
            sparse_query_vector = self.embed_query_sparse(query)

            # Perform vector search
            query_response = await self.qdrant_client.query_points_groups(
                collection_name=self.settings.HANSARD_CONTRIBUTIONS_COLLECTION,
                prefetch=[
                    models.Prefetch(
                        query=dense_query_vector,
                        using="text_dense",
                        limit=max_results * 2,
                    ),
                    models.Prefetch(
                        query=sparse_query_vector,
                        using="text_sparse",
                        limit=max_results * 2,
                    ),
                ],
                query=models.FusionQuery(
                    fusion=models.Fusion.RRF,
                ),
                limit=max_results,
                query_filter=query_filter,
                with_payload=True,
                group_by="DebateSectionExtId",
                group_size=5,
            )
        else:
            # If no query, use scroll to get results with filters only
            query_response = await self.qdrant_client.query_points_groups(
                collection_name=self.settings.HANSARD_CONTRIBUTIONS_COLLECTION,
                query_filter=query_filter,
                limit=max_results,
                with_payload=True,
                group_by="DebateSectionExtId",
                group_size=5,
            )

        relevant_debates = []
        for group in query_response.groups:
            first_hit = group.hits[0].payload
            # If there are less than 2 hits, the debate is not relevant
            if len(group.hits) <= MINIMUM_DEBATE_HITS:
                continue

            debate = {
                "debate_id": first_hit.get("DebateSectionExtId"),
                "title": first_hit.get("DebateSection"),
                "date": first_hit.get("SittingDate"),
                "house": first_hit.get("House"),
                "debate_parents": first_hit.get("debate_parents", []),
            }

            if query:
                debate["relevance_score"] = sum(hit.score for hit in group.hits)
            relevant_debates.append(debate)

        def debate_sorter(debate: dict) -> float:
            """
            Sort debates by relevance score, then by date.
            """
            relevance_score = debate.get("relevance_score", 0)
            debate_date = debate.get("date", "")
            return (relevance_score, debate_date)

        return sorted(relevant_debates, key=debate_sorter, reverse=True)[:max_results]

    async def search_hansard_contributions(
        self,
        query: str | None = None,
        memberId: int | None = None,  # noqa: N803
        dateFrom: str | None = None,  # noqa: N803
        dateTo: str | None = None,  # noqa: N803
        debateId: str | None = None,  # noqa: N803
        house: Literal["Commons", "Lords"] | None = None,
        maxResults: int = 100,  # noqa: N803
        min_score: float = 0.3,
    ) -> list[dict]:
        """
        Search Hansard contributions using Qdrant vector search.

        Args:
            query: Text to search for in contributions (optional)
            memberId: Member ID (optional)
            dateFrom: Start date in format 'YYYY-MM-DD' (optional)
            dateTo: End date in format 'YYYY-MM-DD' (optional)
            debateId: Debate ID (optional)
            house: House (Commons|Lords) (optional)
            maxResults: Maximum number of results to return (default 100)
            min_score: Minimum relevance score (default 0.5)

        Returns:
            List of Hansard contribution details dictionaries

        Raises:
            ValueError: If no search parameters are provided
        """
        # Fail if none of the parameters are provided
        if not query and not memberId and not dateFrom and not dateTo and not debateId and not house:
            msg = "At least one of 'query', 'memberId', 'dateFrom', 'dateTo', 'debateId' or 'house' must be provided"
            raise ValueError(msg)

        # Build filters
        filter_conditions = [
            build_match_filter("MemberId", memberId),
            build_match_filter("DebateSectionExtId", debateId),
            build_match_filter("House", house),
            build_date_range_filter(dateFrom, dateTo),
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
                        limit=maxResults,
                    ),
                    models.Prefetch(
                        query=sparse_query_vector,
                        using="text_sparse",
                        limit=maxResults,
                    ),
                ],
                query=models.FusionQuery(
                    fusion=models.Fusion.RRF,
                ),
                limit=maxResults,
                score_threshold=min_score,
                query_filter=query_filter,
                with_payload=True,
            )
        else:
            # If no query, use scroll to get results with filters only
            query_response = await self.qdrant_client.query_points(
                collection_name=self.settings.HANSARD_CONTRIBUTIONS_COLLECTION,
                query_filter=query_filter,
                limit=maxResults,
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

    async def search_parliamentary_questions(
        self,
        query: str | None = None,
        dateFrom: str | None = None,  # noqa: N803
        dateTo: str | None = None,  # noqa: N803
        party: str | None = None,
        member_name: str | None = None,
        member_id: int | None = None,
        min_score: float = 0.3,
        max_results: int = 100,
    ) -> list[dict]:
        """
        Search Parliamentary Questions using Qdrant vector search.

        Args:
            query: Text to search for in parliamentary questions
            dateFrom: Start date in format 'YYYY-MM-DD' (optional)
            dateTo: End date in format 'YYYY-MM-DD' (optional)
            party: Filter by party (optional)
            member_name: Filter by member name (optional)
            member_id: Filter by member id (optional)
            min_score: Minimum relevance score (default 0.5)
            max_results: Maximum number of results to return (default 100)
        """
        # Build filters
        filter_conditions = [
            build_date_range_filter(dateFrom, dateTo, "dateTabled"),
            build_match_filter("askingMember.party", party),
            build_match_filter("askingMember.name", member_name),
            build_match_filter("askingMember.id", member_id),
        ]

        query_filter = build_filters(filter_conditions)

        if query:
            # Generate embedding for search query
            dense_query_vector = await self.embed_query_dense(query)
            sparse_query_vector = self.embed_query_sparse(query)

            # Perform vector search
            query_response = await self.qdrant_client.query_points(
                collection_name=self.settings.PARLIAMENTARY_QUESTIONS_COLLECTION,
                prefetch=[
                    models.Prefetch(
                        query=dense_query_vector,
                        using="text_dense",
                        limit=max_results,
                    ),
                    models.Prefetch(
                        query=sparse_query_vector,
                        using="text_sparse",
                        limit=max_results,
                    ),
                ],
                query=models.FusionQuery(
                    fusion=models.Fusion.RRF,
                ),
                limit=max_results,
                score_threshold=min_score,
                query_filter=query_filter,
                with_payload=True,
            )
        else:
            # If no query, use scroll to get results with filters only
            query_response = await self.qdrant_client.query_points(
                collection_name=self.settings.PARLIAMENTARY_QUESTIONS_COLLECTION,
                query_filter=query_filter,
                limit=max_results,
                with_payload=True,
            )

        results = []
        for result in query_response.points:
            payload = result.payload

            def safe_get_text(field_value: Any) -> str:
                if isinstance(field_value, dict):
                    return field_value.get("text", "")
                return str(field_value) if field_value is not None else ""

            def parse_date(date_str: str | None) -> str | None:
                if not date_str:
                    return None
                try:
                    return datetime.fromisoformat(date_str).isoformat()
                except (ValueError, TypeError):
                    return None

            # Extract data from payload - now always full documents
            question_text = safe_get_text(payload.get("questionText"))
            answer_text = safe_get_text(payload.get("answerText"))

            results.append(
                {
                    "uin": safe_get_text(payload.get("uin")),
                    "score": result.score if hasattr(result, "score") else 1.0,
                    "questionText": question_text,
                    "answerText": answer_text,
                    "askingMember": payload.get("askingMember"),
                    "answeringMember": payload.get("answeringMember"),
                    "dateTabled": parse_date(payload.get("dateTabled")),
                    "dateAnswered": parse_date(payload.get("dateAnswered")),
                }
            )

        # Sort by relevance score if we have a query
        if query:
            results.sort(key=lambda x: x["score"], reverse=True)

        return results
