from typing import Literal

from pydantic import BaseModel, Field, HttpUrl


class AnalyzeRequest(BaseModel):
    repository_url: HttpUrl


class AnalyzeAccepted(BaseModel):
    job_id: str
    status: Literal["queued"]


class JobState(BaseModel):
    job_id: str
    status: Literal["queued", "running", "complete", "failed"]
    stage: str
    progress: int = Field(ge=0, le=100)
    message: str
    repository: str | None = None
    error_code: str | None = None


class RepositoryNode(BaseModel):
    id: str
    label: str
    kind: str
    path: str | None = None
    description: str | None = None
    metadata: dict[str, str | int | bool | None] = Field(default_factory=dict)


class RepositoryEdge(BaseModel):
    id: str
    source: str
    target: str
    kind: str
    label: str


class RepositoryGraph(BaseModel):
    repository: dict[str, str | int | bool | None]
    nodes: list[RepositoryNode]
    edges: list[RepositoryEdge]
    coverage: dict[str, int | bool | str]


class RepositoryFile(BaseModel):
    path: str
    language: str
    content: str
    truncated: bool = False


class RepositorySummary(BaseModel):
    owner: str
    repository: str
    full_name: str
    description: str | None = None
    default_branch: str
    indexed_at: str
    is_saved: bool = False
