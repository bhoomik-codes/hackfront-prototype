import logging
import secrets
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from uuid import uuid4

from fastapi import BackgroundTasks, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from neo4j import AsyncGraphDatabase
from neo4j.exceptions import Neo4jError

from .config import get_settings
from .github import GitHubError, GitHubIndexer, parse_repository_url
from .graph import (
    delete_repository_graph,
    index_repository,
    list_workspace_repositories,
    read_repository_graph,
)
from .schemas import (
    AnalyzeAccepted,
    AnalyzeRequest,
    JobState,
    RepositoryFile,
    RepositoryGraph,
    RepositorySummary,
)
from .sessions import SESSION_COOKIE, SESSION_MAX_AGE, create_session, verify_session

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s"
)
logger = logging.getLogger("why.api")
settings = get_settings()
if settings.neo4j_configured and (
    len(settings.session_secret) < 32
    or "replace-with" in settings.session_secret.lower()
    or "change-me" in settings.session_secret.lower()
):
    raise RuntimeError(
        "Set SESSION_SECRET to a private random value of at least 32 characters. "
        "A stable secret is required to preserve and safely delete browser-scoped Neo4j data."
    )
session_secret = settings.session_secret or secrets.token_urlsafe(48)


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.driver = None
    app.state.jobs: dict[str, dict[str, object]] = {}
    app.state.import_limits: defaultdict[str, deque[float]] = defaultdict(deque)
    app.state.request_limits: defaultdict[str, deque[float]] = defaultdict(deque)
    app.state.active_jobs = set()
    if settings.neo4j_configured:
        app.state.driver = AsyncGraphDatabase.driver(
            settings.neo4j_uri,
            auth=(settings.neo4j_username, settings.neo4j_password),
            connection_timeout=8,
        )
    yield
    if app.state.driver is not None:
        await app.state.driver.close()


app = FastAPI(
    title="Why Repository Intelligence API",
    version="0.1.0",
    docs_url=None,
    redoc_url=None,
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type"],
    max_age=600,
)


@app.middleware("http")
async def secure_api_response(request: Request, call_next):
    client_key = request.client.host if request.client else "unknown-client"
    now = time.monotonic()
    requests = request.app.state.request_limits[client_key]
    while requests and now - requests[0] > 60:
        requests.popleft()
    if len(requests) >= 120:
        response = JSONResponse(
            status_code=429,
            content={
                "detail": {
                    "code": "request_rate_limit",
                    "message": "Too many requests. Wait a minute and try again.",
                }
            },
        )
    else:
        requests.append(now)
        if request.method == "OPTIONS" or request.url.path == "/api/session":
            response = await call_next(request)
        else:
            workspace_id = verify_session(
                request.cookies.get(SESSION_COOKIE), session_secret
            )
            if workspace_id is None:
                response = JSONResponse(
                    status_code=401,
                    content={
                        "detail": {
                            "code": "workspace_session_required",
                            "message": "Start a browser workspace before using the repository API.",
                        }
                    },
                )
            elif request.method in {"POST", "DELETE", "PUT", "PATCH"}:
                origin = request.headers.get("origin")
                if origin not in settings.origins:
                    response = JSONResponse(
                        status_code=403,
                        content={
                            "detail": {
                                "code": "origin_not_allowed",
                                "message": "This request origin is not allowed.",
                            }
                        },
                    )
                else:
                    request.state.workspace_id = workspace_id
                    response = await call_next(request)
            else:
                request.state.workspace_id = workspace_id
                response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    response.headers["Content-Security-Policy"] = (
        "default-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"
    )
    response.headers["Cache-Control"] = "no-store"
    return response


def _owner_key(request: Request) -> str:
    return str(request.state.workspace_id)


def _driver(request: Request):
    driver = request.app.state.driver
    if driver is None:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "neo4j_not_configured",
                "message": "Configure a Neo4j Aura instance to analyze a repository.",
            },
        )
    return driver


def _update_job(request: Request, job_id: str, **changes: object) -> None:
    job = request.app.state.jobs.get(job_id)
    if job is not None:
        job.update(changes)


def _read_job(request: Request, job_id: str) -> JobState:
    job = request.app.state.jobs.get(job_id)
    if not job or not secrets.compare_digest(
        str(job.get("owner", "")), _owner_key(request)
    ):
        raise HTTPException(
            status_code=404,
            detail={"code": "job_not_found", "message": "Indexing job not found."},
        )
    return JobState(**{key: value for key, value in job.items() if key != "owner"})


async def _analyze_job(
    request: Request, job_id: str, owner: str, repository: str, repository_url: str
) -> None:
    _update_job(
        request,
        job_id,
        status="running",
        stage="connect",
        progress=4,
        message="Connecting to Neo4j and GitHub",
    )
    indexer = GitHubIndexer(settings)
    try:
        driver = _driver(request)
        await driver.verify_connectivity()

        async def update_progress(stage: str, progress: int, message: str) -> None:
            _update_job(
                request, job_id, stage=stage, progress=progress, message=message
            )

        graph_data = await indexer.collect(owner, repository, update_progress)
        counts = await index_repository(
            driver, settings.neo4j_database, graph_data, _owner_key(request)
        )
        repo_data = graph_data.get("repository", {})
        full_name = (
            repo_data.get("full_name", f"{owner}/{repository}")
            if isinstance(repo_data, dict)
            else f"{owner}/{repository}"
        )
        coverage = graph_data.get("coverage", {})
        _update_job(
            request,
            job_id,
            status="complete",
            stage="complete",
            progress=100,
            message="Repository graph is ready",
            repository=str(full_name),
            counts=counts,
            coverage=coverage,
            repository_url=repository_url,
        )
    except GitHubError as error:
        _update_job(
            request,
            job_id,
            status="failed",
            stage="failed",
            progress=100,
            message=error.message,
            error_code=error.code,
        )
    except Neo4jError:
        logger.exception("Neo4j operation failed while indexing a repository")
        _update_job(
            request,
            job_id,
            status="failed",
            stage="failed",
            progress=100,
            message="Neo4j could not save the repository graph. Check the Aura connection and try again.",
            error_code="neo4j_error",
        )
    except HTTPException as error:
        detail = error.detail if isinstance(error.detail, dict) else {}
        _update_job(
            request,
            job_id,
            status="failed",
            stage="failed",
            progress=100,
            message=str(detail.get("message", "Indexing could not start.")),
            error_code=str(detail.get("code", "configuration_error")),
        )
    except Exception:
        logger.exception("Unexpected repository indexing failure")
        _update_job(
            request,
            job_id,
            status="failed",
            stage="failed",
            progress=100,
            message="The repository could not be indexed. Review the server configuration and try again.",
            error_code="indexing_failed",
        )
    finally:
        await indexer.close()
        request.app.state.active_jobs.discard(job_id)


@app.get("/api/health")
async def health(request: Request) -> dict[str, object]:
    driver = request.app.state.driver
    if driver is None:
        return {
            "status": "degraded",
            "neo4j_configured": False,
            "message": "Add Neo4j Aura credentials to enable live repository indexing.",
        }
    try:
        await driver.verify_connectivity()
    except Neo4jError:
        return {
            "status": "degraded",
            "neo4j_configured": True,
            "message": "The graph database is not reachable.",
        }
    return {"status": "ready", "neo4j_configured": True}


@app.get("/api/session")
async def browser_session(request: Request) -> dict[str, bool]:
    existing = verify_session(request.cookies.get(SESSION_COOKIE), session_secret)
    if existing is None:
        _workspace_id, token = create_session(session_secret)
        response = JSONResponse({"ready": True})
        response.set_cookie(
            SESSION_COOKIE,
            token,
            max_age=SESSION_MAX_AGE,
            httponly=True,
            secure=settings.secure_session_cookie,
            samesite="strict",
            path="/api",
        )
        return response
    return {"ready": True}


@app.post("/api/repositories/analyze", response_model=AnalyzeAccepted, status_code=202)
async def analyze_repository(
    payload: AnalyzeRequest, background_tasks: BackgroundTasks, request: Request
) -> AnalyzeAccepted:
    driver = _driver(request)
    try:
        await driver.verify_connectivity()
    except Neo4jError as error:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "neo4j_unavailable",
                "message": "Neo4j Aura is configured but not reachable.",
            },
        ) from error

    owner, repository = parse_repository_url(str(payload.repository_url))
    client_key = _owner_key(request)
    now = time.monotonic()
    limit = request.app.state.import_limits[client_key]
    while limit and now - limit[0] > 3600:
        limit.popleft()
    if len(limit) >= 4:
        raise HTTPException(
            status_code=429,
            detail={
                "code": "import_rate_limit",
                "message": "You have reached the repository import limit. Try again in an hour.",
            },
        )
    if len(request.app.state.active_jobs) >= 2:
        raise HTTPException(
            status_code=429,
            detail={
                "code": "indexing_busy",
                "message": "The indexer is busy. Wait for the current repository analysis to finish.",
            },
        )

    for stale_id, stale_job in list(request.app.state.jobs.items()):
        created_at = float(stale_job.get("created_at", now))
        if now - created_at > 21_600 and stale_id not in request.app.state.active_jobs:
            request.app.state.jobs.pop(stale_id, None)
    if len(request.app.state.jobs) >= 100:
        oldest = sorted(
            (
                (key, job)
                for key, job in request.app.state.jobs.items()
                if key not in request.app.state.active_jobs
            ),
            key=lambda item: float(item[1].get("created_at", 0)),
        )
        for key, _job in oldest[: len(request.app.state.jobs) - 99]:
            request.app.state.jobs.pop(key, None)

    job_id = uuid4().hex
    request.app.state.jobs[job_id] = {
        "job_id": job_id,
        "status": "queued",
        "stage": "queued",
        "progress": 0,
        "message": "Waiting to start",
        "repository": f"{owner}/{repository}",
        "error_code": None,
        "owner": client_key,
        "created_at": now,
    }
    request.app.state.active_jobs.add(job_id)
    limit.append(now)
    background_tasks.add_task(
        _analyze_job, request, job_id, owner, repository, str(payload.repository_url)
    )
    return AnalyzeAccepted(job_id=job_id, status="queued")


@app.get("/api/jobs/{job_id}", response_model=JobState)
async def get_job(job_id: str, request: Request) -> JobState:
    return _read_job(request, job_id)


@app.get("/api/repositories", response_model=list[RepositorySummary])
async def list_repositories(request: Request) -> list[RepositorySummary]:
    driver = _driver(request)
    try:
        repositories = await list_workspace_repositories(
            driver, settings.neo4j_database, _owner_key(request)
        )
    except Neo4jError as error:
        logger.exception("Neo4j repository list query failed")
        raise HTTPException(
            status_code=503,
            detail={
                "code": "repository_list_failed",
                "message": "Your indexed repositories could not be loaded.",
            },
        ) from error
    return [RepositorySummary(**repository) for repository in repositories]


@app.get("/api/repositories/{owner}/{repository}/graph", response_model=RepositoryGraph)
async def get_repository_graph(
    owner: str, repository: str, request: Request, mode: str = "architecture"
) -> RepositoryGraph:
    if mode not in {"architecture", "decisions"}:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "invalid_graph_mode",
                "message": "Graph mode must be architecture or decisions.",
            },
        )
    parsed_owner, parsed_repo = parse_repository_url(
        f"https://github.com/{owner}/{repository}"
    )
    repository_id = (
        f"{_owner_key(request)}:github.com/{parsed_owner.lower()}/{parsed_repo.lower()}"
    )
    driver = _driver(request)
    try:
        graph = await read_repository_graph(
            driver, settings.neo4j_database, repository_id, mode
        )
    except Neo4jError as error:
        logger.exception("Neo4j graph query failed")
        raise HTTPException(
            status_code=503,
            detail={
                "code": "graph_query_failed",
                "message": "The repository graph could not be loaded.",
            },
        ) from error
    if not graph["nodes"]:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "repository_not_indexed",
                "message": "This repository has not been indexed yet.",
            },
        )
    return RepositoryGraph(**graph)


@app.get(
    "/api/repositories/{owner}/{repository}/files/{path:path}",
    response_model=RepositoryFile,
)
async def get_repository_file(
    owner: str, repository: str, path: str, request: Request
) -> RepositoryFile:
    parsed_owner, parsed_repo = parse_repository_url(
        f"https://github.com/{owner}/{repository}"
    )
    repository_id = (
        f"{_owner_key(request)}:github.com/{parsed_owner.lower()}/{parsed_repo.lower()}"
    )
    driver = _driver(request)
    try:
        rows, _, _ = await driver.execute_query(
            "MATCH (r:Repository {id: $id}) RETURN r.default_branch AS branch LIMIT 1",
            id=repository_id,
            database_=settings.neo4j_database,
        )
    except Neo4jError as error:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "graph_query_failed",
                "message": "The repository graph could not be loaded.",
            },
        ) from error
    if not rows:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "repository_not_indexed",
                "message": "This repository has not been indexed yet.",
            },
        )
    indexer = GitHubIndexer(settings)
    try:
        file_data = await indexer.get_file(
            parsed_owner, parsed_repo, str(rows[0]["branch"]), path
        )
    except GitHubError as error:
        raise HTTPException(
            status_code=error.status_code,
            detail={"code": error.code, "message": error.message},
        ) from error
    finally:
        await indexer.close()
    return RepositoryFile(**file_data)


@app.delete("/api/repositories/{owner}/{repository}", status_code=204)
async def delete_repository(owner: str, repository: str, request: Request) -> None:
    parsed_owner, parsed_repo = parse_repository_url(
        f"https://github.com/{owner}/{repository}"
    )
    repository_id = (
        f"{_owner_key(request)}:github.com/{parsed_owner.lower()}/{parsed_repo.lower()}"
    )
    driver = _driver(request)
    try:
        await delete_repository_graph(driver, settings.neo4j_database, repository_id)
    except Neo4jError as error:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "graph_delete_failed",
                "message": "The repository graph could not be removed.",
            },
        ) from error
