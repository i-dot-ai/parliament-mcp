import asyncio
import hashlib
import logging
import os
import tempfile
import uuid
from collections.abc import Generator
from contextlib import contextmanager
from datetime import timedelta
from itertools import chain
from pathlib import Path
from typing import Literal

import hishel
import httpx
from aiolimiter import AsyncLimiter
from async_lru import alru_cache
from chonkie import RecursiveChunker
from fastembed import SparseTextEmbedding
from qdrant_client import AsyncQdrantClient
from qdrant_client.models import PointStruct, SparseVector
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TaskProgressColumn,
    TextColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
)

from parliament_mcp.embedding_helpers import embed_batch, get_openai_client
from parliament_mcp.models import (
    ContributionsResponse,
    DebateParent,
    ParliamentaryQuestion,
    ParliamentaryQuestionsResponse,
    QdrantDocument,
)
from parliament_mcp.settings import ParliamentMCPSettings, settings

logger = logging.getLogger(__name__)

HANSARD_BASE_URL = "https://hansard-api.parliament.uk"
PQS_BASE_URL = "https://questions-statements-api.parliament.uk/api"

# HTTP rate limiter
_http_client_rate_limiter = AsyncLimiter(max_rate=settings.HTTP_MAX_RATE_PER_SECOND, time_period=1.0)


async def cached_limited_get(*args, **kwargs) -> httpx.Response:
    """
    A wrapper around httpx.get that caches the result and limits the rate of requests.
    """
    # Use /tmp for cache in Lambda environment
    if os.environ.get("AWS_LAMBDA_FUNCTION_NAME"):
        # In Lambda, use tempfile to get the temp directory securely
        cache_dir = str(Path(tempfile.gettempdir()) / ".cache" / "hishel")
    else:
        cache_dir = ".cache/hishel"

    async with (
        hishel.AsyncCacheClient(
            timeout=120,
            headers={"User-Agent": "parliament-mcp"},
            storage=hishel.AsyncFileStorage(base_path=cache_dir, ttl=timedelta(days=1).total_seconds()),
            transport=httpx.AsyncHTTPTransport(retries=3),
        ) as client,
        _http_client_rate_limiter,
    ):
        return await client.get(*args, **kwargs)


@alru_cache(maxsize=128, typed=True)
async def load_section_trees(date: str, house: Literal["Commons", "Lords"]) -> dict[int, dict]:
    """
    Loads the debate hierarchy (i.e. section trees) for a given date and house.

    Note: This sits outside the hansard loader because we don't want to cache 'self'

    Args:
        date: The date to load the debate hierarchy for.
        house: The house to load the debate hierarchy for.

    Returns:
        A dictionary of debate parents. Maps both the section id and the external id to the section data.
    """
    url = f"{HANSARD_BASE_URL}/overview/sectionsforday.json"
    response = await cached_limited_get(url, params={"house": house, "date": date})
    response.raise_for_status()
    sections = response.json()

    section_tree_items = []
    for section in sections:
        url = f"{HANSARD_BASE_URL}/overview/sectiontrees.json"
        response = await cached_limited_get(url, params={"section": section, "date": date, "house": house})
        response.raise_for_status()
        section_tree = response.json()
        for item in section_tree:
            section_tree_items.extend(item.get("SectionTreeItems", []))

    # Create a mapping of ID to item for easy lookup
    # Map both the section id and the external id to the section data
    section_tree_map = {}
    for item in section_tree_items:
        section_tree_map[item["Id"]] = item
        section_tree_map[item["ExternalId"]] = item
    return section_tree_map


class QdrantDataLoader:
    """Base class for loading data into Qdrant with progress tracking."""

    def __init__(self, qdrant_client: AsyncQdrantClient, collection_name: str, settings: ParliamentMCPSettings):
        self.qdrant_client = qdrant_client
        self.collection_name = collection_name
        self.settings = settings
        self.progress: Progress | None = None
        self.openai_client = get_openai_client(self.settings)

        self.chunker = RecursiveChunker()
        self.sparse_text_embedding = SparseTextEmbedding(model_name="Qdrant/bm25")

    async def get_total_results(self, url: str, params: dict, count_key: str = "TotalResultCount") -> int:
        """Get total results count from API endpoint"""
        count_params = {**params, "take": 1, "skip": 0}
        response = await cached_limited_get(url, params=count_params)
        response.raise_for_status()
        data = response.json()
        if count_key not in data:
            msg = f"Count key {count_key} not found in response: {data}"
            raise ValueError(msg)
        return data[count_key]

    @contextmanager
    def progress_context(self) -> Generator[Progress, None, None]:
        """Context manager for rich progress bar display."""
        if self.progress is not None:
            yield self.progress

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            MofNCompleteColumn(),
            TaskProgressColumn(),
            TextColumn("Elapsed: "),
            TimeElapsedColumn(),
            TextColumn("Remaining: "),
            TimeRemainingColumn(),
            expand=True,
        ) as progress:
            self.progress = progress
            yield progress
            self.progress.refresh()
        self.progress = None

    def _generate_point_id(self, point_id_str: str) -> str:
        """Generate a consistent UUID from string ID."""
        # Use SHA-256 hash to create consistent UUID
        hash_obj = hashlib.sha256(point_id_str.encode())
        hash_hex = hash_obj.hexdigest()
        # Use first 32 characters to create UUID
        return str(uuid.UUID(hash_hex[:32]))

    async def store_in_qdrant_batch(self, documents: list[QdrantDocument]) -> None:
        """Store documents in Qdrant as chunked embeddings."""
        # Convert documents to chunks
        chunked_documents = list(chain.from_iterable(document.to_chunks(self.chunker) for document in documents))

        if not chunked_documents:
            logger.debug("No chunks to store")
            return

        # Generate embeddings for chunks
        chunk_texts = [chunk["text"] for chunk in chunked_documents]
        embedded_chunks = await embed_batch(
            client=self.openai_client,
            texts=chunk_texts,
            model=self.settings.AZURE_OPENAI_EMBEDDING_MODEL,
            dimensions=self.settings.EMBEDDING_DIMENSIONS,
        )

        sparse_embeddings = list(self.sparse_text_embedding.embed(chunk_texts))

        # Create points for chunks
        points = [
            PointStruct(
                id=self._generate_point_id(chunk["chunk_id"]),
                vector={
                    "text_sparse": SparseVector(
                        indices=sparse_embedding.indices,
                        values=sparse_embedding.values,
                    ),
                    "text_dense": dense_embedding,
                },
                payload=chunk,
            )
            for chunk, dense_embedding, sparse_embedding in zip(
                chunked_documents, embedded_chunks, sparse_embeddings, strict=True
            )
        ]

        # Upsert the chunks
        await self.qdrant_client.upsert(
            collection_name=self.collection_name,
            points=points,
            wait=False,
        )

        logger.debug("Stored %d chunks in collection %s", len(points), self.collection_name)


class QdrantHansardLoader(QdrantDataLoader):
    """Loader for Hansard parliamentary debate contributions using Qdrant."""

    def __init__(
        self,
        page_size: int = 100,
        *args,
        **kwargs,
    ):
        self.page_size = page_size
        super().__init__(*args, **kwargs)

    async def load_all_contributions(
        self,
        from_date: str = "2020-01-01",
        to_date: str = "2020-02-10",
    ) -> None:
        """Load all contribution types concurrently."""
        contribution_types = ["Spoken", "Written", "Corrections", "Petitions"]
        with self.progress_context():
            async with asyncio.TaskGroup() as tg:
                for contrib_type in contribution_types:
                    tg.create_task(self.load_contributions_by_type(contrib_type, from_date, to_date))

    async def load_contributions_by_type(
        self,
        contribution_type: Literal["Spoken", "Written", "Corrections", "Petitions"] = "Spoken",
        from_date: str = "2025-01-01",
        to_date: str = "2025-01-10",
    ) -> None:
        """Load specific contribution type with pagination."""
        base_params = {
            "orderBy": "SittingDateAsc",
            "startDate": from_date,
            "endDate": to_date,
        }

        url = f"{HANSARD_BASE_URL}/search/contributions/{contribution_type}.json"
        total_results = await self.get_total_results(url, base_params | {"take": 1, "skip": 0}, "TotalResultCount")
        task = self.progress.add_task(
            f"Loading '{contribution_type}' contributions",
            total=total_results,
            completed=0,
        )
        if total_results == 0:
            self.progress.update(task, completed=total_results)
            return

        semaphore = asyncio.Semaphore(5)

        async def process_page(query_params: dict):
            """Fetch and process a single page"""
            try:
                async with semaphore:
                    response = await cached_limited_get(url, params=query_params)
                    response.raise_for_status()
                    page_data = response.json()

                    contributions = ContributionsResponse.model_validate(page_data)
                    valid_contributions = [c for c in contributions.Results if len(c.ContributionTextFull) > 0]

                    for contribution in valid_contributions:
                        contribution.debate_parents = await self.get_debate_parents(
                            contribution.SittingDate.strftime("%Y-%m-%d"),
                            contribution.House,
                            contribution.DebateSectionExtId,
                        )

                    await self.store_in_qdrant_batch(valid_contributions)
                    self.progress.update(task, advance=len(contributions.Results))
            except Exception:
                logger.exception("Failed to process page - %s", query_params)
                raise

        # TaskGroup with one task per page
        async with asyncio.TaskGroup() as tg:
            for skip in range(0, total_results, self.page_size):
                tg.create_task(process_page(base_params | {"take": self.page_size, "skip": skip}))

    async def get_debate_parents(
        self, date: str, house: Literal["Commons", "Lords"], debate_ext_id: str
    ) -> list[DebateParent]:
        """Get debate parent hierarchy for a contribution."""
        try:
            section_tree_map = await load_section_trees(date, house)

            # Use the external id rather than the section id because external ids are more stable
            next_id = debate_ext_id
            debate_parents = []
            while next_id is not None:
                if next_id not in section_tree_map:
                    break
                parent = DebateParent.model_validate(section_tree_map[next_id])
                debate_parents.append(parent)
                next_id = parent.ParentId

        except Exception:
            logger.exception("Failed to get debate parents for %s", debate_ext_id)
            return []
        else:
            return debate_parents


class QdrantParliamentaryQuestionLoader(QdrantDataLoader):
    """Loader for Parliamentary Questions using Qdrant."""

    def __init__(
        self,
        page_size: int = 50,
        *args,
        **kwargs,
    ):
        self.page_size = page_size
        super().__init__(*args, **kwargs)

    async def load_questions_for_date_range(
        self,
        from_date: str = "2025-01-01",
        to_date: str = "2025-01-10",
    ) -> None:
        """Load Parliamentary Questions for a date range."""
        seen_ids = set()
        with self.progress_context():
            # Load by both tabled and answered dates sequentially to benefit from seen_ids deduplication
            await self._load_questions_by_date_type("tabled", from_date, to_date, seen_ids)
            await self._load_questions_by_date_type("answered", from_date, to_date, seen_ids)

    async def _load_questions_by_date_type(
        self, date_type: Literal["tabled", "answered"], from_date: str, to_date: str, seen_ids: set[str]
    ) -> None:
        """Load questions filtered by specific date type."""

        base_params = {
            "expandMember": "true",
            f"{date_type}WhenFrom": from_date,
            f"{date_type}WhenTo": to_date,
        }

        url = f"{PQS_BASE_URL}/writtenquestions/questions"
        total_results = await self.get_total_results(url, base_params | {"take": 1, "skip": 0}, "totalResults")

        task = self.progress.add_task(
            f"Loading PQs ({date_type} {from_date} - {to_date})",
            total=total_results,
            completed=0,
        )

        if total_results == 0:
            self.progress.update(task, completed=total_results)
            return

        semaphore = asyncio.Semaphore(3)

        async def process_page(query_params: dict):
            """Fetch and process a single page"""
            try:
                async with semaphore:
                    response = await cached_limited_get(url, params=query_params)
                    response.raise_for_status()
                    page_data = response.json()

                    questions_response = ParliamentaryQuestionsResponse.model_validate(page_data)

                    # Filter out duplicates
                    new_questions = []
                    for question in questions_response.questions:
                        question_id = f"pq_{question.id}"
                        if question_id not in seen_ids:
                            seen_ids.add(question_id)

                            # Enrich truncated questions
                            if await self._needs_enrichment(question):
                                enriched_question = await self.enrich_question(question)
                                new_questions.append(enriched_question)
                            else:
                                new_questions.append(question)

                    if new_questions:
                        await self.store_in_qdrant_batch(new_questions)

                    self.progress.update(task, advance=len(questions_response.questions))
            except Exception:
                logger.exception("Failed to process PQ page - %s", query_params)
                raise

        # TaskGroup with one task per page
        async with asyncio.TaskGroup() as tg:
            for skip in range(0, total_results, self.page_size):
                tg.create_task(process_page(base_params | {"take": self.page_size, "skip": skip}))

    async def _needs_enrichment(self, question: ParliamentaryQuestion) -> bool:
        """Check if question needs content enrichment."""
        return (question.questionText and question.questionText.endswith("...")) or (
            question.answerText and question.answerText.endswith("...")
        )

    async def enrich_question(self, question: ParliamentaryQuestion) -> ParliamentaryQuestion:
        """Fetch full question content when truncated."""
        try:
            url = f"{PQS_BASE_URL}/writtenquestions/questions/{question.id}"
            response = await cached_limited_get(url)
            response.raise_for_status()
            full_question_data = response.json()
            return ParliamentaryQuestion.model_validate(full_question_data["value"])
        except Exception:
            logger.exception("Failed to enrich question %s", question.id)
            return question
