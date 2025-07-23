import hashlib
from collections.abc import Generator
from datetime import datetime
from typing import TypedDict

from chonkie import BaseChunker
from pydantic import BaseModel, ConfigDict, Field, computed_field, field_serializer, field_validator


class ChunkDict(TypedDict):
    """Type for chunk dictionaries that guarantees a 'text', 'chunk_type', and 'chunk_id' property."""

    text: str
    chunk_type: str
    chunk_id: str


class DebateParent(BaseModel):
    """Model for debate parent hierarchy information."""

    model_config = ConfigDict(extra="ignore")

    Id: int
    Title: str
    ParentId: int | None
    ExternalId: str


class QdrantDocument(BaseModel):
    """Base class for Qdrant documents with document URI."""

    created_at: datetime = Field(default_factory=datetime.now)

    @computed_field
    @property
    def document_uri(self) -> str:
        message = "Subclasses must implement this method"
        raise NotImplementedError(message)

    @property
    def get_embeddable_text(self) -> str:
        message = "Subclasses must implement this method"
        raise NotImplementedError(message)

    def to_chunks(self, chunker: BaseChunker) -> Generator[dict]:
        message = "Subclasses must implement this method"
        raise NotImplementedError(message)


class Contribution(QdrantDocument):
    """Model for Hansard contributions/speeches in Parliament."""

    model_config = ConfigDict(extra="forbid")

    MemberName: str | None = None
    MemberId: int | None = None
    AttributedTo: str | None = None
    ItemId: int | None = None
    ContributionExtId: str | None = None
    ContributionText: str | None = None
    ContributionTextFull: str | None = None
    HRSTag: str | None = None
    HansardSection: str | None = None
    DebateSection: str | None = None
    DebateSectionId: int | None = None
    DebateSectionExtId: str | None = None
    SittingDate: datetime | None = None
    Section: str | None = None
    House: str | None = None
    OrderInDebateSection: int | None = None
    DebateSectionOrder: int | None = None
    Rank: int | None = None
    Timecode: datetime | None = None
    debate_parents: list[DebateParent] | None = None

    @computed_field
    @property
    def debate_url(self) -> str:
        return f"https://hansard.parliament.uk/{self.House}/{self.SittingDate:%Y-%m-%d}/debates/{self.DebateSectionExtId}/link"

    @computed_field
    @property
    def contribution_url(self) -> str:
        if self.ContributionExtId is None:
            return None
        return f"{self.debate_url}#contribution-{self.ContributionExtId}"

    @computed_field
    @property
    def document_uri(self) -> str:
        if self.ContributionExtId is None:
            # if external id is None, then use a hash of the text and order in section
            doc_hash = hashlib.sha256(
                f"{self.DebateSectionExtId}_{self.ContributionText}_{self.OrderInDebateSection}".encode()
            ).hexdigest()
            return f"debate_{self.DebateSectionExtId}_contrib_{doc_hash}"
        else:
            return f"debate_{self.DebateSectionExtId}_contrib_{self.ContributionExtId}"

    @property
    def get_embeddable_text(self) -> str:
        return self.ContributionTextFull

    def __str__(self):
        """String representation of contribution."""
        res = f"\nContribution {self.OrderInDebateSection}"
        res += f"\nSpeaker: {self.AttributedTo}"
        res += f"\n{self.ContributionText}\n"

        return res

    def to_chunks(self, chunker: BaseChunker) -> Generator[ChunkDict]:
        chunks = chunker.chunk(self.ContributionTextFull)
        document = self.model_dump(mode="json")
        del document["ContributionTextFull"]
        del document["ContributionText"]
        for chunk_id, chunk in enumerate(chunks):
            chunk_dict: ChunkDict = {
                **document,
                "text": chunk.text,
                "chunk_type": "contribution",
                "chunk_id": f"{self.document_uri}_chunk_{chunk_id}",
            }
            yield chunk_dict


class ContributionChunk(BaseModel):
    """Model for a chunk of a contribution."""

    text: str
    contribution: Contribution


class ContributionsResponse(BaseModel):
    """API response model for contributions list."""

    Results: list[Contribution]
    TotalResultCount: int

    model_config = ConfigDict(extra="ignore")


# Parliamentary Questions


class Member(BaseModel):
    """
    Represents a parliamentary member with their associated details.

    Attributes:
        id: Unique identifier for the member
        listAs: The member's listing name
        name: Full name of the member
        party: Political party affiliation
        partyColour: Colour associated with the party
        partyAbbreviation: Short form of the party name
        memberFrom: Constituency or area represented
        thumbnailUrl: URL to member's thumbnail image
    """

    id: int
    listAs: str | None = None
    name: str | None = None
    party: str | None = None
    partyColour: str | None = None
    partyAbbreviation: str | None = None
    memberFrom: str | None = None
    thumbnailUrl: str | None = None


class Attachment(BaseModel):
    """
    Represents an attachment associated with a parliamentary question.

    Attributes:
        url: Location of the attachment
        title: Name or description of the attachment
        fileType: Format of the attachment
        fileSizeBytes: Size of the attachment in bytes
    """

    url: str | None = None
    title: str | None = None
    fileType: str | None = None
    fileSizeBytes: int | None = None


class GroupedQuestionDate(BaseModel):
    """
    Represents a grouped question with its date information.

    Attributes:
        questionUin: Unique identifier for the question
        dateTabled: When the question was submitted
    """

    questionUin: str | None = None
    dateTabled: datetime

    @field_validator("dateTabled", mode="before")
    @classmethod
    def parse_datetime(cls, value) -> datetime:
        """Convert ISO format datetime string to datetime object."""
        if isinstance(value, str):
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        return value


class ParliamentaryQuestion(QdrantDocument):
    """
    Represents a parliamentary question with its associated metadata and answer.

    This model includes information about the asking member, answering member,
    question content, answer content, and various timestamps and status flags.
    """

    model_config = ConfigDict(extra="ignore")

    id: int
    askingMemberId: int
    askingMember: Member | None = None
    house: str
    memberHasInterest: bool
    dateTabled: datetime
    dateForAnswer: datetime | None = None
    uin: str | None = None
    questionText: str | None = None
    answeringBodyId: int
    answeringBodyName: str | None = None
    isWithdrawn: bool
    isNamedDay: bool
    groupedQuestions: list[str] = []
    answerIsHolding: bool | None = None
    answerIsCorrection: bool | None = None
    answeringMemberId: int | None = None
    answeringMember: Member | None = None
    correctingMemberId: int | None = None
    correctingMember: Member | None = None
    dateAnswered: datetime | None = None
    answerText: str | None = None
    originalAnswerText: str | None = None
    comparableAnswerText: str | None = None
    dateAnswerCorrected: datetime | None = None
    dateHoldingAnswer: datetime | None = None
    attachmentCount: int
    heading: str | None = None
    attachments: list[Attachment] = []
    groupedQuestionsDates: list[GroupedQuestionDate] = []
    created_at: datetime = Field(default_factory=datetime.now)

    @field_serializer(
        "dateTabled",
        "dateForAnswer",
        "dateAnswered",
        "dateAnswerCorrected",
        "dateHoldingAnswer",
    )
    def serialize_datetime(self, dt: datetime | None) -> str | None:
        """Serialize datetime fields to ISO format strings."""
        return dt.isoformat() if dt else None

    @computed_field
    @property
    def document_uri(self) -> str:
        return f"pq_{self.id}"

    @property
    def is_truncated(self) -> bool:
        """Check if question/answer text is truncated."""
        return (self.questionText is not None and self.questionText.endswith("...")) or (
            self.answerText is not None and self.answerText.endswith("...")
        )

    @property
    def get_embeddable_text(self) -> str:
        return f"QUESTION: {self.questionText}\n ANSWER: {self.answerText}".strip()

    def to_chunks(self, chunker: BaseChunker) -> Generator[ChunkDict]:
        question_chunks = chunker.chunk(self.questionText)
        answer_chunks = chunker.chunk(self.answerText)
        document = self.model_dump(mode="json")
        del document["questionText"]
        del document["answerText"]
        for chunk_id, chunk in enumerate(question_chunks, start=0):
            chunk_dict: ChunkDict = {
                **document,
                "text": chunk.text,
                "chunk_type": "question",
                "chunk_id": f"{self.document_uri}_chunk_{chunk_id}",
            }
            yield chunk_dict
        for chunk_id, chunk in enumerate(answer_chunks, start=len(question_chunks)):
            chunk_dict: ChunkDict = {
                **document,
                "text": chunk.text,
                "chunk_type": "answer",
                "chunk_id": f"{self.document_uri}_chunk_{chunk_id}",
            }
            yield chunk_dict


class Link(BaseModel):
    """
    Represents an API link with its properties.

    Attributes:
        rel: Relationship type of the link
        href: URL of the link
        method: HTTP method to be used
    """

    rel: str
    href: str
    method: str


class PQResultItem(BaseModel):
    """
    Represents the API response for a question query.

    Attributes:
        value: The parliamentary question data
        links: Related API links
    """

    value: ParliamentaryQuestion
    links: list[Link]


class ParliamentaryQuestionsResponse(BaseModel):
    results: list[PQResultItem]
    totalResults: int

    model_config = ConfigDict(extra="ignore")

    @property
    def questions(self) -> list[ParliamentaryQuestion]:
        """Extract questions from results."""
        return [item.value for item in self.results]
