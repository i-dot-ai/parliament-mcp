import asyncio
import logging
from collections.abc import Generator
from contextlib import contextmanager
from datetime import timedelta
from typing import Literal

import hishel
import httpx
from aiolimiter import AsyncLimiter
from async_lru import alru_cache
from elasticsearch import AsyncElasticsearch
from elasticsearch.helpers import BulkIndexError, async_bulk
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

from parliament_mcp.models import (
    ContributionsResponse,
    DebateParent,
    ElasticDocument,
    ParliamentaryQuestion,
    ParliamentaryQuestionsResponse,
)
from parliament_mcp.settings import ParliamentMCPSettings, settings

logger = logging.getLogger(__name__)

HANSARD_BASE_URL = "https://hansard-api.parliament.uk"
PQS_BASE_URL = "https://questions-statements-api.parliament.uk/api"


_http_client_rate_limiter = AsyncLimiter(max_rate=settings.HTTP_MAX_RATE_PER_SECOND, time_period=1.0)


async def cached_limited_get(*args, **kwargs) -> httpx.Response:
    """
    A wrapper around httpx.get that caches the result and limits the rate of requests.
    """
    async with (
        hishel.AsyncCacheClient(
            timeout=30,
            headers={"User-Agent": "parliament-mcp"},
            storage=hishel.AsyncFileStorage(ttl=timedelta(days=1).total_seconds()),
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
        A dictionary of debate parents.
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
    return {item["Id"]: item for item in section_tree_items}


class ElasticDataLoader:
    """Base class for loading data into Elasticsearch with progress tracking."""

    def __init__(self, elastic_client: AsyncElasticsearch, index_name: str):
        self.elastic_client = elastic_client
        self.index_name = index_name
        self.progress: Progress | None = None

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

    async def store_in_elastic(self, data: list[ElasticDocument]) -> None:
        """Bulk store documents in Elasticsearch with retries."""
        try:
            actions = [
                {
                    "_op_type": "index",
                    "_index": self.index_name,
                    "_id": item.document_uri,
                    "_source": item.model_dump(mode="json"),
                }
                for item in data
            ]

            await async_bulk(
                self.elastic_client,
                actions=actions,
                max_retries=3,
            )
        except BulkIndexError as e:
            raise e from e


class ElasticHansardLoader(ElasticDataLoader):
    """Loader for Hansard parliamentary debate contributions."""

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
        total_results = await self.get_total_results(url, base_params | {"take": 1, "skip": 0})
        task = self.progress.add_task(f"Loading '{contribution_type}' contributions", total=total_results, completed=0)
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
                            contribution.DebateSectionId,
                        )

                    await self.store_in_elastic(valid_contributions)
                    self.progress.update(task, advance=len(contributions.Results))
            except Exception:
                logger.exception("Failed to process page - %s", query_params)

        # TaskGroup with one task per page
        async with asyncio.TaskGroup() as tg:
            for skip in range(0, total_results, self.page_size):
                tg.create_task(process_page(base_params | {"take": self.page_size, "skip": skip}))

    async def get_debate_parents(
        self, date: str, house: Literal["Commons", "Lords"], debate_section_id: int
    ) -> list[DebateParent]:
        """Retrieve parent debate hierarchy for a contribution."""
        try:
            section_tree_for_date = await load_section_trees(date, house)
            debate_parents = []
            next_id = debate_section_id
            while next_id is not None:
                parent = DebateParent.model_validate(section_tree_for_date[next_id])
                debate_parents.append(parent)
                next_id = parent.ParentId
        except Exception:
            logger.exception("Failed to get debate parents for debate section - %s", debate_section_id)
            return []
        else:
            return debate_parents


class ElasticParliamentaryQuestionLoader(ElasticDataLoader):
    """
    Handles the loading and processing of parliamentary questions from the API.

    This class manages HTTP requests with rate limiting and caching, and handles
    the fetching of both summarised and full question content when needed.
    """

    def __init__(
        self,
        page_size: int = 50,
        *args,
        **kwargs,
    ):
        self.page_size = page_size
        super().__init__(*args, **kwargs)

    async def enrich_question(self, question: ParliamentaryQuestion) -> ParliamentaryQuestion:
        """
        Fetch the full version of a question when the summary is truncated.

        Args:
            question_id: The unique identifier of the question

        Returns:
            ParliamentaryQuestion: The full question data or original if fetch fails

        Raises:
            ValueError: If no original question is stored
        """
        try:
            response = await cached_limited_get(
                f"{PQS_BASE_URL}/writtenquestions/questions/{question.id}",
                params={"expandMember": "true"},
            )
            response.raise_for_status()

            data = response.json()
            new_question = ParliamentaryQuestion(**data["value"])
            question.questionText = new_question.questionText
            question.answerText = new_question.answerText
        except Exception:
            logger.exception("Failed to fetch full question - %s", question.id)
        return question

    async def load_questions_for_date_range(
        self,
        from_date: str = "2020-01-01",
        to_date: str = "2020-02-10",
    ) -> None:
        """
        Load questions within the specified date range, checking both tabled and answered dates.
        """
        url = f"{PQS_BASE_URL}/writtenquestions/questions"

        semaphore = asyncio.Semaphore(5)
        seen_ids = set()

        async def process_page(query_params: dict, task_id: int):
            """Fetch and process a single page"""
            try:
                async with semaphore:
                    response = await cached_limited_get(url, params=query_params)
                    response.raise_for_status()
                    page_data = response.json()

                    response = ParliamentaryQuestionsResponse.model_validate(page_data)
                    valid_questions = []

                    for question in response.questions:
                        if question.id in seen_ids:
                            continue

                        seen_ids.add(question.id)

                        if question.is_truncated:
                            enriched_question = await self.enrich_question(question)
                            valid_questions.append(enriched_question)
                        else:
                            valid_questions.append(question)

                    await self.store_in_elastic(valid_questions)
                    self.progress.update(task_id, advance=len(response.questions))
            except Exception:
                logger.exception("Failed to process page - %s", query_params)

        with self.progress_context():
            tabled_task_id = self.progress.add_task("Loading 'tabled' questions", total=0, completed=0, start=True)
            answered_task_id = self.progress.add_task("Loading 'answered' questions", total=0, completed=0, start=False)

            async with asyncio.TaskGroup() as tg:
                questions_tabled_params = {
                    "tabledWhenFrom": from_date,
                    "tabledWhenTo": to_date,
                    "expandMember": "true",
                    "take": self.page_size,
                }
                total_results = await self.get_total_results(url, questions_tabled_params, count_key="totalResults")
                self.progress.start_task(tabled_task_id)
                self.progress.update(tabled_task_id, total=total_results)
                for skip in range(0, total_results, self.page_size):
                    tg.create_task(process_page(questions_tabled_params | {"skip": skip}, tabled_task_id))

            async with asyncio.TaskGroup() as tg:
                questions_answered_params = {
                    "answeredWhenFrom": from_date,
                    "answeredWhenTo": to_date,
                    "expandMember": "true",
                    "take": self.page_size,
                }
                total_results = await self.get_total_results(url, questions_answered_params, count_key="totalResults")
                self.progress.start_task(answered_task_id)
                self.progress.update(answered_task_id, total=total_results)
                for skip in range(0, total_results, self.page_size):
                    tg.create_task(process_page(questions_answered_params | {"skip": skip}, answered_task_id))


async def load_data(
    es_client: AsyncElasticsearch, settings: ParliamentMCPSettings, source: str, from_date: str, to_date: str
):
    """Load data from specified source into Elasticsearch within date range.

    Args:
        settings: ParliamentMCPSettings instance
        source: Data source - either "hansard" or "parliamentary-questions"
        from_date: Start date in YYYY-MM-DD format
        to_date: End date in YYYY-MM-DD format
        elastic_client: Optional Elasticsearch client (will create one if not provided)
    """
    if source == "hansard":
        loader = ElasticHansardLoader(elastic_client=es_client, index_name=settings.HANSARD_CONTRIBUTIONS_INDEX)
        await loader.load_all_contributions(from_date, to_date)
    elif source == "parliamentary-questions":
        loader = ElasticParliamentaryQuestionLoader(
            elastic_client=es_client, index_name=settings.PARLIAMENTARY_QUESTIONS_INDEX
        )
        await loader.load_questions_for_date_range(from_date, to_date)
