import asyncio
import copy
import re
import time
from collections import OrderedDict
from pathlib import PurePosixPath
from urllib.parse import quote, urlsplit

import httpx

from .config import Settings

GITHUB_API = "https://api.github.com"
RAW_GITHUB = "https://raw.githubusercontent.com"
SOURCE_CACHE_TTL_SECONDS = 300
SOURCE_CACHE_MAX_ENTRIES = 128
SOURCE_CHUNK_BYTES = 16_384
API_CACHE_TTL_SECONDS = 180
API_CACHE_MAX_ENTRIES = 256
_API_CACHE: OrderedDict[
    tuple[str, tuple[tuple[str, str], ...]], tuple[float, object]
] = OrderedDict()
_API_RATE_LIMITED_UNTIL = 0.0
_SOURCE_CACHE: OrderedDict[
    tuple[str, str, str, str], tuple[float, dict[str, str | bool]]
] = OrderedDict()
REPOSITORY_PART = re.compile(r"^[A-Za-z0-9_.-]{1,100}$")
SUPPORTED_SOURCE = {
    ".c",
    ".cc",
    ".cpp",
    ".cs",
    ".go",
    ".h",
    ".hpp",
    ".java",
    ".js",
    ".jsx",
    ".mjs",
    ".php",
    ".py",
    ".rb",
    ".rs",
    ".scala",
    ".swift",
    ".ts",
    ".tsx",
    ".vue",
    ".svelte",
    ".json",
    ".toml",
    ".yaml",
    ".yml",
}
SKIP_PARTS = {
    ".git",
    ".next",
    ".nuxt",
    "build",
    "coverage",
    "dist",
    "generated",
    "node_modules",
    "out",
    "vendor",
}
JS_IMPORT = re.compile(
    r"(?:import\s+(?:[^'\"]+?\s+from\s+)?|export\s+[^'\"]+?\s+from\s+|require\s*\()\s*['\"]([^'\"]+)['\"]"
)
PY_IMPORT = re.compile(
    r"^\s*(?:from\s+([\w.]+)\s+import\s+|import\s+([\w.]+))", re.MULTILINE
)


class GitHubError(Exception):
    def __init__(self, code: str, message: str, status_code: int = 502) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


def parse_repository_url(value: str) -> tuple[str, str]:
    parts = urlsplit(str(value))
    if (
        parts.scheme != "https"
        or parts.hostname != "github.com"
        or parts.port
        or parts.username
        or parts.password
    ):
        raise GitHubError(
            "invalid_repository_url",
            "Enter a public repository URL from github.com.",
            422,
        )
    if parts.query or parts.fragment:
        raise GitHubError(
            "invalid_repository_url",
            "Repository URLs cannot include query parameters or fragments.",
            422,
        )
    segments = [segment for segment in parts.path.split("/") if segment]
    if len(segments) != 2:
        raise GitHubError(
            "invalid_repository_url",
            "Use a repository URL in the form https://github.com/owner/repository.",
            422,
        )
    owner, repository = segments
    if repository.lower().endswith(".git"):
        repository = repository[:-4]
    if (
        not REPOSITORY_PART.fullmatch(owner)
        or not REPOSITORY_PART.fullmatch(repository)
        or owner in {".", ".."}
        or repository in {".", ".."}
    ):
        raise GitHubError(
            "invalid_repository_url",
            "The repository URL contains an invalid owner or name.",
            422,
        )
    return owner, repository


def _clean_text(value: object, maximum: int = 12_000) -> str:
    if not isinstance(value, str):
        return ""
    return value.replace("\x00", "")[:maximum]


def _valid_repo_path(path: str) -> bool:
    parts = PurePosixPath(path).parts
    return (
        bool(path)
        and not path.startswith("/")
        and all(part not in {"", ".", ".."} for part in parts)
    )


def _language(path: str) -> str:
    extension = PurePosixPath(path).suffix.lower()
    return {
        ".c": "C",
        ".cc": "C++",
        ".cpp": "C++",
        ".cs": "C#",
        ".go": "Go",
        ".h": "C/C++",
        ".hpp": "C++",
        ".java": "Java",
        ".js": "JavaScript",
        ".jsx": "JavaScript",
        ".mjs": "JavaScript",
        ".php": "PHP",
        ".py": "Python",
        ".rb": "Ruby",
        ".rs": "Rust",
        ".scala": "Scala",
        ".swift": "Swift",
        ".ts": "TypeScript",
        ".tsx": "TypeScript",
        ".vue": "Vue",
        ".svelte": "Svelte",
        ".json": "JSON",
        ".toml": "TOML",
        ".yaml": "YAML",
        ".yml": "YAML",
    }.get(extension, "Other")


def _import_targets(path: str, content: str, indexed_paths: set[str]) -> set[str]:
    extension = PurePosixPath(path).suffix.lower()
    candidates = (
        JS_IMPORT.findall(content)
        if extension in {".js", ".jsx", ".mjs", ".ts", ".tsx", ".vue", ".svelte"}
        else []
    )
    result: set[str] = set()
    for specifier in candidates:
        if not specifier.startswith("."):
            continue
        parent = PurePosixPath(path).parent
        base = parent.joinpath(specifier)
        guesses = {base.as_posix()}
        if not PurePosixPath(specifier).suffix:
            guesses.update(
                f"{base.as_posix()}{suffix}"
                for suffix in (
                    ".ts",
                    ".tsx",
                    ".js",
                    ".jsx",
                    ".mjs",
                    ".vue",
                    ".svelte",
                    ".json",
                )
            )
        guesses.update(
            f"{base.as_posix()}/index{suffix}"
            for suffix in (".ts", ".tsx", ".js", ".jsx")
        )
        result.update(candidate for candidate in guesses if candidate in indexed_paths)
    if extension == ".py":
        for match in PY_IMPORT.finditer(content):
            module = match.group(1) or match.group(2)
            if not module:
                continue
            relative = (
                PurePosixPath(path).parent / module.replace(".", "/")
            ).as_posix()
            guesses = {f"{relative}.py", f"{relative}/__init__.py"}
            result.update(
                candidate for candidate in guesses if candidate in indexed_paths
            )
    return result


class GitHubIndexer:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        api_headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "Why-Repository-Intelligence",
        }
        if settings.github_api_token:
            api_headers["Authorization"] = f"Bearer {settings.github_api_token}"
        self.api = httpx.AsyncClient(
            base_url=GITHUB_API,
            timeout=httpx.Timeout(settings.github_api_timeout_seconds),
            headers=api_headers,
            follow_redirects=False,
        )
        self.raw = httpx.AsyncClient(
            timeout=httpx.Timeout(settings.github_api_timeout_seconds),
            follow_redirects=False,
        )
        self.api_semaphore = asyncio.Semaphore(5)
        self.request_count = 0

    async def close(self) -> None:
        await self.api.aclose()
        await self.raw.aclose()

    async def _get(
        self, endpoint: str, params: dict[str, str | int] | None = None
    ) -> object:
        global _API_RATE_LIMITED_UNTIL
        cache_key = (
            endpoint,
            tuple(sorted((str(key), str(value)) for key, value in (params or {}).items())),
        )
        now = time.monotonic()
        cached = _API_CACHE.get(cache_key)
        if cached is not None:
            expires_at, payload = cached
            if expires_at > now:
                _API_CACHE.move_to_end(cache_key)
                return copy.deepcopy(payload)
            del _API_CACHE[cache_key]
        if now < _API_RATE_LIMITED_UNTIL:
            wait_seconds = max(1, int(_API_RATE_LIMITED_UNTIL - now))
            raise GitHubError(
                "github_rate_limited",
                f"GitHub’s API limit was reached. Try again in about {wait_seconds // 60 + 1} minutes.",
                429,
            )
        self.request_count += 1
        if self.request_count > 24:
            raise GitHubError(
                "github_request_budget",
                "This import reached its safe GitHub history request limit. Try again later.",
                429,
            )
        try:
            async with self.api_semaphore:
                response = await self.api.get(endpoint, params=params)
        except httpx.TimeoutException as error:
            raise GitHubError(
                "github_timeout",
                "GitHub did not respond in time. Try again shortly.",
                504,
            ) from error
        except httpx.HTTPError as error:
            raise GitHubError(
                "github_unavailable",
                "GitHub could not be reached. Check the connection and try again.",
                502,
            ) from error
        if response.status_code == 404:
            raise GitHubError(
                "repository_not_found", "Repository not found or it is not public.", 404
            )
        if (
            response.status_code == 403
            and response.headers.get("x-ratelimit-remaining") == "0"
        ):
            reset_at = response.headers.get("x-ratelimit-reset")
            try:
                reset_epoch = int(reset_at) if reset_at else 0
            except ValueError:
                reset_epoch = 0
            delay = max(60, reset_epoch - int(time.time())) if reset_epoch else 3600
            _API_RATE_LIMITED_UNTIL = time.monotonic() + delay
            raise GitHubError(
                "github_rate_limited",
                f"GitHub’s API limit was reached. Try again in about {delay // 60 + 1} minutes.",
                429,
            )
        if response.status_code in {401, 403}:
            raise GitHubError(
                "github_access_denied",
                "GitHub denied access to this public repository.",
                403,
            )
        if response.status_code == 429:
            raise GitHubError(
                "github_rate_limited",
                "GitHub temporarily limited this import. Try again later.",
                429,
            )
        if response.status_code >= 400:
            raise GitHubError(
                "github_api_error",
                f"GitHub returned an error ({response.status_code}).",
                502,
            )
        payload = response.json()
        _API_CACHE[cache_key] = (time.monotonic() + API_CACHE_TTL_SECONDS, payload)
        _API_CACHE.move_to_end(cache_key)
        while len(_API_CACHE) > API_CACHE_MAX_ENTRIES:
            _API_CACHE.popitem(last=False)
        return copy.deepcopy(payload)

    async def _raw_file(
        self,
        owner: str,
        repository: str,
        ref: str,
        path: str,
        semaphore: asyncio.Semaphore,
    ) -> tuple[str, str]:
        if not _valid_repo_path(path):
            return path, ""
        url = f"{RAW_GITHUB}/{quote(owner, safe='')}/{quote(repository, safe='')}/{quote(ref, safe='/')}/{quote(path, safe='/')}"
        async with semaphore:
            try:
                response = await self.raw.get(
                    url,
                    headers={
                        "Accept": "text/plain",
                        "User-Agent": "Why-Repository-Intelligence",
                    },
                )
            except httpx.HTTPError:
                return path, ""
        if (
            response.status_code != 200
            or len(response.content) > self.settings.max_source_bytes
        ):
            return path, ""
        try:
            return path, response.content.decode("utf-8")
        except UnicodeDecodeError:
            return path, ""

    async def get_file(
        self, owner: str, repository: str, ref: str, path: str
    ) -> dict[str, str | bool]:
        if not _valid_repo_path(path) or len(path) > 500:
            raise GitHubError("invalid_file_path", "The file path is invalid.", 422)
        cache_key = (owner.lower(), repository.lower(), ref, path)
        now = time.monotonic()
        cached = _SOURCE_CACHE.get(cache_key)
        if cached is not None:
            expires_at, source = cached
            if expires_at > now:
                _SOURCE_CACHE.move_to_end(cache_key)
                return dict(source)
            del _SOURCE_CACHE[cache_key]

        url = f"{RAW_GITHUB}/{quote(owner, safe='')}/{quote(repository, safe='')}/{quote(ref, safe='/')}/{quote(path, safe='/')}"
        chunks: list[bytes] = []
        byte_count = 0
        truncated = False
        try:
            async with self.raw.stream(
                "GET",
                url,
                headers={
                    "Accept": "text/plain",
                    "User-Agent": "Why-Repository-Intelligence",
                },
            ) as response:
                if response.status_code == 404:
                    raise GitHubError(
                        "file_not_found",
                        "The file does not exist at this revision.",
                        404,
                    )
                if response.status_code in {429, 403}:
                    raise GitHubError(
                        "github_rate_limited",
                        "GitHub temporarily limited this file request. Try again later.",
                        429,
                    )
                if response.status_code >= 400:
                    raise GitHubError(
                        "github_file_unavailable",
                        "GitHub could not return this file. Try again shortly.",
                        502,
                    )
                content_length = response.headers.get("content-length")
                if content_length and content_length.isdigit():
                    truncated = int(content_length) > self.settings.max_source_bytes
                async for chunk in response.aiter_bytes(SOURCE_CHUNK_BYTES):
                    remaining = self.settings.max_source_bytes + 1 - byte_count
                    if remaining <= 0:
                        truncated = True
                        break
                    chunks.append(chunk[:remaining])
                    byte_count += min(len(chunk), remaining)
                    if (
                        len(chunk) > remaining
                        or byte_count > self.settings.max_source_bytes
                    ):
                        truncated = True
                        break
        except httpx.TimeoutException as error:
            raise GitHubError(
                "github_timeout",
                "GitHub did not respond in time. Try again shortly.",
                504,
            ) from error
        except httpx.HTTPError as error:
            raise GitHubError(
                "github_unavailable",
                "GitHub could not be reached. Check the connection and try again.",
                502,
            ) from error
        raw_content = b"".join(chunks)
        bounded_content = raw_content[: self.settings.max_source_bytes]
        try:
            content = bounded_content.decode("utf-8", errors="strict")
        except UnicodeDecodeError as error:
            if truncated and error.end == len(bounded_content):
                content = bounded_content[: error.start].decode("utf-8")
            else:
                raise GitHubError(
                    "unsupported_file", "This file is not valid UTF-8 text.", 415
                ) from error
        if "\x00" in content:
            raise GitHubError(
                "unsupported_file", "This file is not valid UTF-8 text.", 415
            )
        source = {
            "path": path,
            "language": _language(path),
            "content": content,
            "truncated": truncated or byte_count > self.settings.max_source_bytes,
        }
        _SOURCE_CACHE[cache_key] = (
            time.monotonic() + SOURCE_CACHE_TTL_SECONDS,
            source,
        )
        _SOURCE_CACHE.move_to_end(cache_key)
        while len(_SOURCE_CACHE) > SOURCE_CACHE_MAX_ENTRIES:
            _SOURCE_CACHE.popitem(last=False)
        return dict(source)

    async def collect(
        self, owner: str, repository: str, progress: object
    ) -> dict[str, object]:
        prefix = f"/repos/{quote(owner, safe='')}/{quote(repository, safe='')}"
        repo_data = await self._get(prefix)
        if not isinstance(repo_data, dict) or repo_data.get("private") is not False:
            raise GitHubError(
                "repository_not_public",
                "Only public GitHub repositories can be analyzed.",
                403,
            )
        default_branch = _clean_text(repo_data.get("default_branch"), 200)
        full_name = _clean_text(repo_data.get("full_name"), 220)
        if not default_branch or not full_name:
            raise GitHubError(
                "invalid_repository_metadata",
                "GitHub returned incomplete repository metadata.",
            )

        await progress("structure", 12, "Reading repository structure")
        tree_data = await self._get(
            f"{prefix}/git/trees/{quote(default_branch, safe='')}?recursive=1"
        )
        if not isinstance(tree_data, dict) or not isinstance(
            tree_data.get("tree"), list
        ):
            raise GitHubError(
                "invalid_repository_tree",
                "GitHub returned an unreadable repository tree.",
            )
        tree_entries = [
            entry
            for entry in tree_data["tree"]
            if isinstance(entry, dict) and isinstance(entry.get("path"), str)
        ]
        if len(tree_entries) > 100_000:
            raise GitHubError(
                "repository_too_large",
                "This repository has too many entries for a safe web import.",
                413,
            )
        tree_truncated = bool(tree_data.get("truncated"))
        indexed_blobs = [
            entry
            for entry in tree_entries
            if entry.get("type") == "blob"
            and _valid_repo_path(entry["path"])
            and not SKIP_PARTS.intersection(PurePosixPath(entry["path"]).parts)
        ]
        indexed_blobs.sort(
            key=lambda entry: (
                0
                if PurePosixPath(entry["path"]).suffix.lower() in SUPPORTED_SOURCE
                else 1,
                entry["path"].count("/"),
                entry["path"].casefold(),
            )
        )
        indexed_blobs = indexed_blobs[: self.settings.max_indexed_files]
        indexed_paths = {entry["path"] for entry in indexed_blobs}

        await progress("history", 26, "Reading commits and pull requests")
        commits_result, pulls_result, issues_result = await asyncio.gather(
            self._get(f"{prefix}/commits", {"per_page": 8}),
            self._get(
                f"{prefix}/pulls",
                {"state": "all", "per_page": 8, "sort": "updated", "direction": "desc"},
            ),
            self._get(
                f"{prefix}/issues",
                {
                    "state": "all",
                    "per_page": 10,
                    "sort": "updated",
                    "direction": "desc",
                },
            ),
        )
        commits = (
            [item for item in commits_result if isinstance(item, dict)]
            if isinstance(commits_result, list)
            else []
        )
        pulls = (
            [item for item in pulls_result if isinstance(item, dict)]
            if isinstance(pulls_result, list)
            else []
        )
        issues = (
            [
                item
                for item in issues_result
                if isinstance(item, dict) and "pull_request" not in item
            ]
            if isinstance(issues_result, list)
            else []
        )

        semaphore = asyncio.Semaphore(4)
        commit_tasks = [
            self._get(
                f"{prefix}/commits/{quote(_clean_text(item.get('sha'), 64), safe='')}"
            )
            for item in commits[:2]
            if _clean_text(item.get("sha"), 64)
        ]
        commit_details = await asyncio.gather(*commit_tasks, return_exceptions=True)
        commit_files: list[dict[str, object]] = []
        for item in commit_details:
            if isinstance(item, BaseException) and isinstance(item, GitHubError):
                raise item
            if isinstance(item, dict) and isinstance(item.get("files"), list):
                sha = _clean_text(item.get("sha"), 64)
                commit_files.extend(
                    {**file, "_commit_sha": sha}
                    for file in item["files"]
                    if isinstance(file, dict)
                )

        await progress("discussions", 48, "Connecting files to review discussions")
        pull_details_tasks = []
        # Keep the most useful discussion evidence while staying well below
        # GitHub's shared anonymous quota on a hosted, multi-user service.
        for pull in pulls[:3]:
            number = pull.get("number")
            if isinstance(number, int) and number > 0:
                pull_details_tasks.extend(
                    [
                        self._get(f"{prefix}/pulls/{number}/files", {"per_page": 100}),
                        self._get(
                            f"{prefix}/pulls/{number}/reviews", {"per_page": 100}
                        ),
                        self._get(
                            f"{prefix}/issues/{number}/comments", {"per_page": 100}
                        ),
                        self._get(
                            f"{prefix}/pulls/{number}/comments", {"per_page": 100}
                        ),
                    ]
                )
        pull_details = await asyncio.gather(*pull_details_tasks, return_exceptions=True)
        failure = next(
            (item for item in pull_details if isinstance(item, GitHubError)), None
        )
        if failure is not None:
            raise failure
        pr_records: list[dict[str, object]] = []
        issues_by_number = {
            item.get("number"): item
            for item in issues
            if isinstance(item.get("number"), int)
        }
        comment_records: list[dict[str, object]] = []
        for index, pull in enumerate(pulls[:3]):
            number = pull.get("number")
            if not isinstance(number, int) or number <= 0:
                continue
            detail_slice = pull_details[index * 4 : (index + 1) * 4]
            files = detail_slice[0] if isinstance(detail_slice[0], list) else []
            reviews = detail_slice[1] if isinstance(detail_slice[1], list) else []
            comments = detail_slice[2] if isinstance(detail_slice[2], list) else []
            review_comments = (
                detail_slice[3] if isinstance(detail_slice[3], list) else []
            )
            pr_records.append(
                {
                    "pull": pull,
                    "files": files,
                    "reviews": reviews,
                    "comments": comments,
                    "review_comments": review_comments,
                }
            )
            for comment in [*reviews, *comments, *review_comments]:
                if (
                    isinstance(comment, dict)
                    and _clean_text(comment.get("body"), 12_000).strip()
                ):
                    comment_records.append({"pull_number": number, **comment})

        for pull in pulls:
            text = f"{_clean_text(pull.get('title'), 500)} {_clean_text(pull.get('body'), 3000)}"
            for issue_number in {
                int(number) for number in re.findall(r"(?<!\w)#(\d{1,8})\b", text)
            }:
                if issue_number in issues_by_number:
                    issues_by_number[issue_number]["referenced_by_pull"] = pull.get(
                        "number"
                    )

        await progress("source_map", 70, "Mapping source-level imports")
        source_candidates = [
            path
            for path in indexed_paths
            if PurePosixPath(path).suffix.lower() in SUPPORTED_SOURCE
            and PurePosixPath(path).suffix.lower()
            not in {".json", ".toml", ".yaml", ".yml"}
        ]
        source_candidates.sort(key=lambda path: (path.count("/"), path.casefold()))
        source_candidates = source_candidates[: self.settings.max_source_files]
        used_bytes = 0
        source_map: dict[str, str] = {}
        for start in range(0, len(source_candidates), 8):
            batch = source_candidates[start : start + 8]
            results = await asyncio.gather(
                *(
                    self._raw_file(owner, repository, default_branch, path, semaphore)
                    for path in batch
                )
            )
            for path, content in results:
                if (
                    content
                    and used_bytes + len(content.encode("utf-8"))
                    <= self.settings.max_source_total_bytes
                ):
                    source_map[path] = content
                    used_bytes += len(content.encode("utf-8"))
        imports: list[tuple[str, str]] = []
        for path, content in source_map.items():
            imports.extend(
                (path, target)
                for target in _import_targets(path, content, indexed_paths)
                if target != path
            )

        await progress("graph", 88, "Saving connected repository history")
        source_tree = {"directories": set(), "files": indexed_blobs}
        for entry in indexed_blobs:
            parent = PurePosixPath(entry["path"]).parent
            while parent != PurePosixPath("."):
                source_tree["directories"].add(parent.as_posix())
                parent = parent.parent
        return {
            "repository": repo_data,
            "default_branch": default_branch,
            "tree_truncated": tree_truncated,
            "directories": sorted(source_tree["directories"]),
            "files": indexed_blobs,
            "source_map": source_map,
            "imports": imports,
            "commits": commits,
            "commit_files": commit_files,
            "pull_requests": pr_records,
            "issues": list(issues_by_number.values()),
            "comments": comment_records,
            "request_count": self.request_count,
            "coverage": {
                "indexed_files": len(indexed_blobs),
                "source_files_parsed": len(source_map),
                "import_relationships": len(imports),
                "commits": len(commits),
                "pull_requests": len(pulls),
                "discussion_records": len(comment_records),
                "issues": len(issues_by_number),
                "tree_truncated": tree_truncated,
                "partial_file_index": len(indexed_blobs)
                >= self.settings.max_indexed_files,
                "history_window": "metadata for latest 8 commits and pull requests; changed-file details for latest 2 commits and latest 3 pull requests",
            },
        }
