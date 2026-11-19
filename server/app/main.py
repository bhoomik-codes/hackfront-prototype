import asyncio
import logging
import secrets
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlencode
from uuid import uuid4

import httpx
from fastapi import BackgroundTasks, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from neo4j import AsyncGraphDatabase
from neo4j.exceptions import DriverError, Neo4jError

from .config import get_settings
from .github import GitHubError, GitHubIndexer, parse_repository_url
from .graph import (
    delete_expired_repository_graphs,
    delete_repository_graph,
    delete_workspace_repositories,
    find_accessible_repository_id,
    find_saved_repository_id,
    index_repository,
    list_workspace_repositories,
    read_repository_graph,
    save_repository_graph,
    touch_repository_graph,
)
from .schemas import (
    AnalyzeAccepted,
    AnalyzeRequest,
    JobState,
    RepositoryFile,
    RepositoryGraph,
    RepositorySummary,
)
from .sessions import (
    OAUTH_STATE_COOKIE,
    SESSION_COOKIE,
    SESSION_MAX_AGE,
    create_oauth_state,
    create_session,
    session_claims,
    verify_oauth_state,
    verify_session,
)

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s"
)
logger = logging.getLogger("why.api")
settings = get_settings()
if (settings.neo4j_configured or settings.github_oauth_configured) and (
    len(settings.session_secret) < 32
    or "replace-with" in settings.session_secret.lower()
    or "change-me" in settings.session_secret.lower()
):
    raise RuntimeError(
        "Set SESSION_SECRET to a private random value of at least 32 characters. "
        "A stable secret is required to preserve and safely delete browser-scoped Neo4j data."
    )
if settings.session_cookie_samesite == "none" and not settings.secure_session_cookie:
    raise RuntimeError(
        "SESSION_COOKIE_SAMESITE=none requires SECURE_SESSION_COOKIE=true."
    )
session_secret = settings.session_secret or secrets.token_urlsafe(48)


async def _expiry_sweeper(driver) -> None:
    while True:
        try:
            removed = await delete_expired_repository_graphs(
                driver, settings.neo4j_database
            )
            if removed:
                logger.info("Removed %s expired temporary repository graph(s)", removed)
        except (DriverError, Neo4jError, ExceptionGroup) as error:
            logger.warning("Temporary graph cleanup failed (%s)", type(error).__name__)
        await asyncio.sleep(60)


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.driver = None
    app.state.jobs: dict[str, dict[str, object]] = {}
    app.state.import_limits: defaultdict[str, deque[float]] = defaultdict(deque)
    app.state.request_limits: defaultdict[str, deque[float]] = defaultdict(deque)
    app.state.active_jobs = set()
    app.state.workspace_locks = {}
    app.state.closed_workspaces = {}
    cleanup_task = None
    if settings.neo4j_configured:
        app.state.driver = AsyncGraphDatabase.driver(
            settings.neo4j_uri,
            auth=(settings.neo4j_username, settings.neo4j_password),
            connection_timeout=8,
        )
        cleanup_task = asyncio.create_task(_expiry_sweeper(app.state.driver))
    try:
        yield
    finally:
        if cleanup_task is not None:
            cleanup_task.cancel()
            try:
                await cleanup_task
            except asyncio.CancelledError:
                pass
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
    if not request.url.path.startswith("/api/"):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Permissions-Policy"] = (
            "camera=(), microphone=(), geolocation=()"
        )
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; connect-src 'self' https:; font-src 'self' data:; "
            "object-src 'none'; base-uri 'self'; frame-ancestors 'none'; "
            "form-action 'self' https://github.com"
        )
        return response
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
        if request.method == "OPTIONS" or request.url.path in {
            "/api/session",
            "/api/auth/github",
            "/api/auth/github/callback",
        }:
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
                own_origin = f"{request.url.scheme}://{request.url.netloc}"
                if origin not in settings.origins and origin != own_origin:
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
                    claims = (
                        session_claims(
                            request.cookies.get(SESSION_COOKIE), session_secret
                        )
                        or {}
                    )
                    request.state.auth_user_id = claims.get("user_id")
                    request.state.auth_login = claims.get("login")
                    response = await call_next(request)
            else:
                request.state.workspace_id = workspace_id
                claims = (
                    session_claims(request.cookies.get(SESSION_COOKIE), session_secret)
                    or {}
                )
                request.state.auth_user_id = claims.get("user_id")
                request.state.auth_login = claims.get("login")
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


def _authenticated_user_id(request: Request) -> str | None:
    user_id = getattr(request.state, "auth_user_id", None)
    return str(user_id) if user_id else None


def _set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=SESSION_MAX_AGE,
        httponly=True,
        secure=settings.secure_session_cookie,
        samesite=settings.session_cookie_samesite,
        path="/api",
    )


def _workspace_closed(request: Request, workspace_id: str) -> bool:
    closed = request.app.state.closed_workspaces
    now = time.monotonic()
    for closed_id, expires_at in list(closed.items()):
        if expires_at <= now:
            closed.pop(closed_id, None)
    return workspace_id in closed


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
        workspace_id = _owner_key(request)
        lock = request.app.state.workspace_locks.setdefault(
            workspace_id, asyncio.Lock()
        )
        async with lock:
            if _workspace_closed(request, workspace_id):
                _update_job(
                    request,
                    job_id,
                    status="failed",
                    stage="cancelled",
                    progress=100,
                    message="The browser workspace closed before indexing finished, so no repository data was kept.",
                    error_code="workspace_closed",
                )
                return
            counts = await index_repository(
                driver, settings.neo4j_database, graph_data, workspace_id
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
    except (DriverError, Neo4jError, ExceptionGroup):
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
    except (DriverError, Neo4jError, ExceptionGroup) as error:
        logger.warning("Neo4j health check failed (%s)", type(error).__name__)
        return {
            "status": "degraded",
            "neo4j_configured": True,
            "message": "The graph database is not reachable.",
        }
    return {"status": "ready", "neo4j_configured": True}


@app.get("/api/session")
async def browser_session(request: Request) -> dict[str, object]:
    token = request.cookies.get(SESSION_COOKIE)
    claims = session_claims(token, session_secret)
    existing = str(claims["workspace_id"]) if claims else None
    if existing is None or _workspace_closed(request, existing):
        _workspace_id, token = create_session(session_secret)
        response = JSONResponse(
            {
                "ready": True,
                "authenticated": False,
                "login": None,
                "oauth_configured": settings.github_oauth_configured,
            }
        )
        _set_session_cookie(response, token)
        return response
    return {
        "ready": True,
        "authenticated": bool(claims.get("user_id")),
        "login": claims.get("login"),
        "oauth_configured": settings.github_oauth_configured,
    }


@app.get("/api/auth/github")
async def begin_github_login():
    if not settings.github_oauth_configured:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "github_oauth_not_configured",
                "message": "GitHub sign-in is not configured on this API yet.",
            },
        )
    state, state_cookie = create_oauth_state(session_secret)
    response = RedirectResponse(
        "https://github.com/login/oauth/authorize?"
        + urlencode(
            {
                "client_id": settings.github_client_id,
                "redirect_uri": settings.github_oauth_callback_url,
                "scope": "read:user",
                "state": state,
            }
        ),
        status_code=302,
    )
    response.set_cookie(
        OAUTH_STATE_COOKIE,
        state_cookie,
        max_age=600,
        httponly=True,
        secure=settings.secure_session_cookie,
        samesite="lax",
        path="/api/auth/github/callback",
    )
    return response


@app.get("/api/auth/github/callback")
async def finish_github_login(request: Request, code: str = "", state: str = ""):
    if not settings.github_oauth_configured:
        return RedirectResponse(
            settings.frontend_url + "?auth=unavailable", status_code=303
        )
    state_cookie = request.cookies.get(OAUTH_STATE_COOKIE)
    if not verify_oauth_state(state_cookie, state, session_secret) or not code:
        response = RedirectResponse(
            settings.frontend_url + "?auth=failed", status_code=303
        )
        response.delete_cookie(OAUTH_STATE_COOKIE, path="/api/auth/github/callback")
        return response
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(8.0), follow_redirects=False
        ) as client:
            token_response = await client.post(
                "https://github.com/login/oauth/access_token",
                headers={"Accept": "application/json"},
                data={
                    "client_id": settings.github_client_id,
                    "client_secret": settings.github_client_secret,
                    "code": code,
                    "redirect_uri": settings.github_oauth_callback_url,
                    "state": state,
                },
            )
            token_response.raise_for_status()
            access_token = token_response.json().get("access_token")
            if not isinstance(access_token, str) or not access_token:
                raise ValueError("GitHub did not return an access token")
            user_response = await client.get(
                "https://api.github.com/user",
                headers={
                    "Accept": "application/vnd.github+json",
                    "Authorization": f"Bearer {access_token}",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
            )
            user_response.raise_for_status()
            github_user = user_response.json()
        github_id = github_user.get("id")
        login = github_user.get("login")
        if not isinstance(github_id, int) or not isinstance(login, str) or not login:
            raise ValueError("GitHub returned an invalid account profile")
    except (httpx.HTTPError, ValueError, KeyError):
        logger.warning("GitHub OAuth sign-in failed")
        response = RedirectResponse(
            settings.frontend_url + "?auth=failed", status_code=303
        )
        response.delete_cookie(OAUTH_STATE_COOKIE, path="/api/auth/github/callback")
        return response
    finally:
        access_token = None
    existing_claims = session_claims(
        request.cookies.get(SESSION_COOKIE), session_secret
    )
    workspace_id = str(existing_claims["workspace_id"]) if existing_claims else None
    if workspace_id and _workspace_closed(request, workspace_id):
        workspace_id = None
    _workspace_id, session_token = create_session(
        session_secret, str(github_id), login, workspace_id
    )
    response = RedirectResponse(
        settings.frontend_url + "?auth=success", status_code=303
    )
    _set_session_cookie(response, session_token)
    response.delete_cookie(OAUTH_STATE_COOKIE, path="/api/auth/github/callback")
    return response


@app.post("/api/auth/logout", status_code=204)
async def logout(request: Request) -> Response:
    workspace_id = _owner_key(request)
    request.app.state.closed_workspaces[workspace_id] = time.monotonic() + 21_600
    lock = request.app.state.workspace_locks.setdefault(workspace_id, asyncio.Lock())
    async with lock:
        if (driver := request.app.state.driver) is not None:
            await delete_workspace_repositories(
                driver, settings.neo4j_database, workspace_id
            )
    _workspace_id, token = create_session(session_secret)
    response = Response(status_code=204)
    _set_session_cookie(response, token)
    return response


@app.post("/api/session/cleanup", status_code=204)
async def cleanup_browser_workspace(request: Request) -> Response:
    workspace_id = _owner_key(request)
    request.app.state.closed_workspaces[workspace_id] = time.monotonic() + 21_600
    lock = request.app.state.workspace_locks.setdefault(workspace_id, asyncio.Lock())
    driver = request.app.state.driver
    async with lock:
        if driver is not None:
            try:
                removed = await delete_workspace_repositories(
                    driver, settings.neo4j_database, workspace_id
                )
                if removed:
                    logger.info(
                        "Removed %s temporary graph(s) for a closing browser workspace",
                        removed,
                    )
            except (DriverError, Neo4jError, ExceptionGroup) as error:
                logger.warning(
                    "Browser workspace cleanup failed (%s)", type(error).__name__
                )
                raise HTTPException(
                    status_code=503,
                    detail={
                        "code": "workspace_cleanup_failed",
                        "message": "Temporary repository data could not be removed yet; automatic expiry cleanup will retry.",
                    },
                ) from error
    _new_workspace_id, token = create_session(session_secret)
    response = Response(status_code=204)
    _set_session_cookie(response, token)
    return response


@app.post("/api/repositories/analyze", response_model=AnalyzeAccepted, status_code=202)
async def analyze_repository(
    payload: AnalyzeRequest, background_tasks: BackgroundTasks, request: Request
) -> AnalyzeAccepted:
    if _workspace_closed(request, _owner_key(request)):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "workspace_closed",
                "message": "This browser workspace has expired. Refresh the app to start a new temporary workspace.",
            },
        )
    driver = _driver(request)
    try:
        await driver.verify_connectivity()
    except (DriverError, Neo4jError, ExceptionGroup) as error:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "neo4j_unavailable",
                "message": "Neo4j Aura is configured but not reachable.",
            },
        ) from error

    owner, repository = parse_repository_url(str(payload.repository_url))
    user_id = _authenticated_user_id(request)
    if user_id:
        try:
            if await find_saved_repository_id(
                driver,
                settings.neo4j_database,
                user_id,
                f"{owner}/{repository}",
            ):
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "repository_already_saved",
                        "message": "This repository is already saved to your account. Open it from your repository list instead of creating a duplicate index.",
                    },
                )
        except (DriverError, Neo4jError, ExceptionGroup) as error:
            raise HTTPException(
                status_code=503,
                detail={
                    "code": "repository_lookup_failed",
                    "message": "Your saved repository list could not be checked.",
                },
            ) from error
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
            driver,
            settings.neo4j_database,
            _owner_key(request),
            _authenticated_user_id(request),
        )
    except (DriverError, Neo4jError, ExceptionGroup) as error:
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
    driver = _driver(request)
    try:
        repository_id = await find_accessible_repository_id(
            driver,
            settings.neo4j_database,
            _owner_key(request),
            _authenticated_user_id(request),
            f"{parsed_owner}/{parsed_repo}",
        )
        if repository_id is None:
            raise HTTPException(
                status_code=404,
                detail={
                    "code": "repository_not_indexed",
                    "message": "Repository index not found.",
                },
            )
        exists = await touch_repository_graph(
            driver,
            settings.neo4j_database,
            repository_id,
            _authenticated_user_id(request),
        )
    except (DriverError, Neo4jError, ExceptionGroup) as error:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "graph_query_failed",
                "message": "The repository graph could not be loaded.",
            },
        ) from error
    if not exists:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "repository_not_indexed",
                "message": "This temporary repository index has expired or was removed.",
            },
        )
    try:
        graph = await read_repository_graph(
            driver, settings.neo4j_database, repository_id, mode
        )
    except (DriverError, Neo4jError, ExceptionGroup) as error:
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
    driver = _driver(request)
    try:
        repository_id = await find_accessible_repository_id(
            driver,
            settings.neo4j_database,
            _owner_key(request),
            _authenticated_user_id(request),
            f"{parsed_owner}/{parsed_repo}",
        )
        if repository_id is None:
            raise HTTPException(
                status_code=404,
                detail={
                    "code": "repository_not_indexed",
                    "message": "Repository index not found.",
                },
            )
        rows, _, _ = await driver.execute_query(
            "MATCH (r:Repository {id: $id}) WHERE r.expires_at > $now OR "
            "(r.is_saved = true AND $user_id IS NOT NULL AND r.owner_user_id = $user_id) "
            "WITH r, CASE WHEN r.is_saved = true THEN null ELSE $expires_at END AS expiry "
            "SET r.expires_at = expiry "
            "RETURN r.default_branch AS branch LIMIT 1",
            id=repository_id,
            now=datetime.now(UTC).isoformat(),
            expires_at=(datetime.now(UTC) + timedelta(hours=2)).isoformat(),
            user_id=_authenticated_user_id(request),
            database_=settings.neo4j_database,
        )
    except (DriverError, Neo4jError, ExceptionGroup) as error:
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
    driver = _driver(request)
    try:
        repository_id = await find_accessible_repository_id(
            driver,
            settings.neo4j_database,
            _owner_key(request),
            _authenticated_user_id(request),
            f"{parsed_owner}/{parsed_repo}",
        )
        if repository_id:
            await delete_repository_graph(
                driver, settings.neo4j_database, repository_id
            )
    except (DriverError, Neo4jError, ExceptionGroup) as error:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "graph_delete_failed",
                "message": "The repository graph could not be removed.",
            },
        ) from error


@app.post("/api/repositories/{owner}/{repository}/save")
async def save_repository(
    owner: str, repository: str, request: Request
) -> dict[str, bool]:
    user_id = _authenticated_user_id(request)
    if not user_id:
        raise HTTPException(
            status_code=401,
            detail={
                "code": "authentication_required",
                "message": "Sign in with GitHub before saving repository indexes.",
            },
        )
    parsed_owner, parsed_repo = parse_repository_url(
        f"https://github.com/{owner}/{repository}"
    )
    driver = _driver(request)
    try:
        repository_id = await find_accessible_repository_id(
            driver,
            settings.neo4j_database,
            _owner_key(request),
            user_id,
            f"{parsed_owner}/{parsed_repo}",
        )
        if repository_id is None or not repository_id.startswith(
            f"{_owner_key(request)}:"
        ):
            raise HTTPException(
                status_code=404,
                detail={
                    "code": "temporary_index_not_found",
                    "message": "Only an index in your current workspace can be saved.",
                },
            )
        saved = await save_repository_graph(
            driver,
            settings.neo4j_database,
            repository_id,
            _owner_key(request),
            user_id,
        )
        if not saved:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "temporary_index_expired",
                    "message": "This temporary index expired before it could be saved.",
                },
            )
    except (DriverError, Neo4jError, ExceptionGroup) as error:
        logger.exception("Neo4j repository save failed")
        raise HTTPException(
            status_code=503,
            detail={
                "code": "repository_save_failed",
                "message": "The repository index could not be saved.",
            },
        ) from error
    return {"saved": True}


frontend_dist = Path(__file__).resolve().parents[2] / "dist"
if frontend_dist.is_dir():
    app.mount("/", StaticFiles(directory=frontend_dist, html=True), name="frontend")
