import asyncio
import base64
import re
from pathlib import PurePosixPath
from urllib.parse import quote, urlsplit

import httpx

from .config import Settings

GITHUB_API = "https://api.github.com"
RAW_GITHUB = "https://raw.githubusercontent.com"
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
        self.api = httpx.AsyncClient(
            base_url=GITHUB_API,
            timeout=httpx.Timeout(settings.github_api_timeout_seconds),
            headers={
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "Why-Repository-Intelligence",
            },
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
        self.request_count += 1
        if self.request_count > 55:
            raise GitHubError(
                "github_request_budget",
                "The import reached its safe GitHub request limit. Try a smaller repository or retry later.",
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
            raise GitHubError(
                "github_rate_limited",
                "GitHub’s public API rate limit was reached. Try again later.",
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
        return response.json()

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
        endpoint = f"/repos/{quote(owner, safe='')}/{quote(repository, safe='')}/contents/{quote(path, safe='/')}"
        response = await self._get(endpoint, {"ref": ref})
        if (
            not isinstance(response, dict)
            or response.get("type") != "file"
            or not isinstance(response.get("content"), str)
        ):
            raise GitHubError(
                "unsupported_file", "This item is not a readable text file.", 415
            )
        encoded = response["content"]
        if len(encoded) > self.settings.max_source_bytes * 2:
            raise GitHubError(
                "file_too_large", "This file is larger than the preview limit.", 413
            )
        try:
            content = base64.b64decode(encoded, validate=False).decode("utf-8")
        except (ValueError, UnicodeDecodeError) as error:
            raise GitHubError(
                "unsupported_file", "This file is not valid UTF-8 text.", 415
            ) from error
        return {
            "path": path,
            "language": _language(path),
            "content": content[: self.settings.max_source_bytes],
            "truncated": len(content) > self.settings.max_source_bytes,
        }

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
            for item in commits
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
        for pull in pulls[:6]:
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
        for index, pull in enumerate(pulls[:6]):
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
                "history_window": "latest 8 commits and pull requests; discussion details for latest 6 pull requests",
            },
        }
