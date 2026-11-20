import hashlib
import re
from datetime import UTC, datetime, timedelta
from typing import Any

from neo4j import AsyncDriver

SCHEMA = (
    "CREATE CONSTRAINT repository_id IF NOT EXISTS FOR (n:Repository) REQUIRE n.id IS UNIQUE",
    "CREATE CONSTRAINT directory_id IF NOT EXISTS FOR (n:Directory) REQUIRE n.id IS UNIQUE",
    "CREATE CONSTRAINT file_id IF NOT EXISTS FOR (n:File) REQUIRE n.id IS UNIQUE",
    "CREATE CONSTRAINT commit_id IF NOT EXISTS FOR (n:Commit) REQUIRE n.id IS UNIQUE",
    "CREATE CONSTRAINT pull_request_id IF NOT EXISTS FOR (n:PullRequest) REQUIRE n.id IS UNIQUE",
    "CREATE CONSTRAINT issue_id IF NOT EXISTS FOR (n:Issue) REQUIRE n.id IS UNIQUE",
    "CREATE CONSTRAINT comment_id IF NOT EXISTS FOR (n:Comment) REQUIRE n.id IS UNIQUE",
    "CREATE CONSTRAINT person_id IF NOT EXISTS FOR (n:Person) REQUIRE n.id IS UNIQUE",
    "CREATE INDEX directory_repository IF NOT EXISTS FOR (n:Directory) ON (n.repository_id)",
    "CREATE INDEX file_repository IF NOT EXISTS FOR (n:File) ON (n.repository_id)",
    "CREATE INDEX commit_repository IF NOT EXISTS FOR (n:Commit) ON (n.repository_id)",
    "CREATE INDEX pull_request_repository IF NOT EXISTS FOR (n:PullRequest) ON (n.repository_id)",
    "CREATE INDEX issue_repository IF NOT EXISTS FOR (n:Issue) ON (n.repository_id)",
    "CREATE INDEX comment_repository IF NOT EXISTS FOR (n:Comment) ON (n.repository_id)",
    "CREATE INDEX person_repository IF NOT EXISTS FOR (n:Person) ON (n.repository_id)",
)
TEMPORARY_GRAPH_TTL = timedelta(hours=2)

_OVERRIDE_TERMS = r"supersed\w*|replac\w*|deprecat\w*|revert\w*|no longer"
_OVERRIDE_BEFORE_REFERENCE = re.compile(
    rf"\b(?:{_OVERRIDE_TERMS})\b[^#\n]{{0,100}}#(\d+)", re.IGNORECASE
)
_REFERENCE_BEFORE_OVERRIDE = re.compile(
    rf"#(\d+)[^\n]{{0,100}}\b(?:{_OVERRIDE_TERMS})\b", re.IGNORECASE
)


def _explicitly_superseded_prs(*texts: object) -> set[int]:
    """Find only nearby, explicit override phrases that name a PR number."""
    targets: set[int] = set()
    for text in texts:
        if not isinstance(text, str):
            continue
        bounded = text[:8_000]
        matches = [
            *_OVERRIDE_BEFORE_REFERENCE.findall(bounded),
            *_REFERENCE_BEFORE_OVERRIDE.findall(bounded),
        ]
        targets.update(int(number) for number in matches if number.isdigit())
    return targets


def _short(value: object, limit: int = 12_000) -> str:
    return value.replace("\x00", "")[:limit] if isinstance(value, str) else ""


def _user(
    person: object, repository_id: str, people: dict[str, dict[str, Any]]
) -> str | None:
    if not isinstance(person, dict):
        return None
    login = _short(person.get("login"), 100)
    if not login:
        return None
    identity = f"{repository_id}:person:{login.lower()}"
    people[identity] = {
        "id": identity,
        "repository_id": repository_id,
        "login": login,
        "url": _short(person.get("html_url"), 500),
    }
    return identity


async def _upsert_nodes(
    transaction: Any, label: str, rows: list[dict[str, Any]]
) -> None:
    if not rows:
        return
    query = f"UNWIND $rows AS row MERGE (n:{label} {{id: row.id}}) SET n += row"
    for start in range(0, len(rows), 400):
        result = await transaction.run(query, rows=rows[start : start + 400])
        await result.consume()


async def _upsert_edges(
    transaction: Any, relationship: str, rows: list[dict[str, str]]
) -> None:
    if not rows:
        return
    query = (
        "UNWIND $rows AS row "
        "MATCH (a {id: row.source, repository_id: $repository_id}) "
        "MATCH (b {id: row.target, repository_id: $repository_id}) "
        f"MERGE (a)-[:{relationship}]->(b)"
    )
    for start in range(0, len(rows), 500):
        result = await transaction.run(
            query,
            rows=rows[start : start + 500],
            repository_id=rows[0]["repository_id"],
        )
        await result.consume()


async def index_repository(
    driver: AsyncDriver, database: str, data: dict[str, object], workspace_id: str
) -> dict[str, int]:
    repository = data["repository"]
    if not isinstance(repository, dict):
        raise TypeError("Repository metadata is missing")
    owner = repository.get("owner") if isinstance(repository.get("owner"), dict) else {}
    owner_name = _short(owner.get("login"), 100)
    full_name = _short(repository.get("full_name"), 220)
    repository_id = f"{workspace_id}:github.com/{full_name.lower()}"
    default_branch = _short(data.get("default_branch"), 200)
    indexed_at = datetime.now(UTC).isoformat()
    expires_at = (datetime.now(UTC) + TEMPORARY_GRAPH_TTL).isoformat()

    async with driver.session(database=database) as session:
        for statement in SCHEMA:
            result = await session.run(statement)
            await result.consume()

    repo_node = [
        {
            "id": repository_id,
            "repository_id": repository_id,
            "name": full_name,
            "owner": owner_name,
            "url": _short(repository.get("html_url"), 500),
            "description": _short(repository.get("description"), 2_000),
            "default_branch": default_branch,
            "stars": repository.get("stargazers_count")
            if isinstance(repository.get("stargazers_count"), int)
            else 0,
            "indexed_at": indexed_at,
            "expires_at": expires_at,
            "is_saved": False,
            "owner_user_id": None,
            "tree_truncated": bool(data.get("tree_truncated")),
            "kind": "repository",
        }
    ]
    directories = data.get("directories", [])
    files = data.get("files", [])
    imports = data.get("imports", [])
    commits = data.get("commits", [])
    changed_files = data.get("commit_files", [])
    pull_requests = data.get("pull_requests", [])
    issues = data.get("issues", [])
    comments = data.get("comments", [])

    directory_rows: list[dict[str, Any]] = []
    containment: list[dict[str, str]] = []
    file_by_path: dict[str, dict[str, Any]] = {}
    for path in directories if isinstance(directories, list) else []:
        if not isinstance(path, str):
            continue
        identity = f"{repository_id}:dir:{path}"
        directory_rows.append(
            {
                "id": identity,
                "repository_id": repository_id,
                "name": path.rsplit("/", 1)[-1],
                "path": path,
                "kind": "module",
                "description": f"Directory in {full_name}",
            }
        )
        parent = path.rsplit("/", 1)[0] if "/" in path else ""
        parent_id = f"{repository_id}:dir:{parent}" if parent else repository_id
        containment.append(
            {"repository_id": repository_id, "source": parent_id, "target": identity}
        )
    for entry in files if isinstance(files, list) else []:
        if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
            continue
        path = entry["path"]
        identity = f"{repository_id}:file:{path}"
        file_row = {
            "id": identity,
            "repository_id": repository_id,
            "name": path.rsplit("/", 1)[-1],
            "path": path,
            "kind": "file",
            "language": _language_from_path(path),
            "description": f"Source file in {full_name}",
        }
        file_by_path[path] = file_row
        parent = path.rsplit("/", 1)[0] if "/" in path else ""
        parent_id = f"{repository_id}:dir:{parent}" if parent else repository_id
        containment.append(
            {"repository_id": repository_id, "source": parent_id, "target": identity}
        )

    for entry in changed_files if isinstance(changed_files, list) else []:
        if not isinstance(entry, dict) or not isinstance(entry.get("filename"), str):
            continue
        path = entry["filename"]
        identity = f"{repository_id}:file:{path}"
        if identity not in {row["id"] for row in file_by_path.values()}:
            file_by_path[path] = {
                "id": identity,
                "repository_id": repository_id,
                "name": path.rsplit("/", 1)[-1],
                "path": path,
                "kind": "file",
                "language": _language_from_path(path),
                "description": "File referenced by repository history",
                "historical_only": True,
            }
            parent = path.rsplit("/", 1)[0] if "/" in path else ""
            parent_id = f"{repository_id}:dir:{parent}" if parent else repository_id
            containment.append(
                {
                    "repository_id": repository_id,
                    "source": parent_id,
                    "target": identity,
                }
            )

    person_nodes: dict[str, dict[str, Any]] = {}
    commit_rows: list[dict[str, Any]] = []
    changes_edges: list[dict[str, str]] = []
    author_edges: list[dict[str, str]] = []
    for commit in commits if isinstance(commits, list) else []:
        if not isinstance(commit, dict):
            continue
        sha = _short(commit.get("sha"), 64)
        details = commit.get("commit") if isinstance(commit.get("commit"), dict) else {}
        author_info = (
            details.get("author") if isinstance(details.get("author"), dict) else {}
        )
        user_id = _user(commit.get("author"), repository_id, person_nodes)
        identity = f"{repository_id}:commit:{sha}"
        commit_rows.append(
            {
                "id": identity,
                "repository_id": repository_id,
                "sha": sha,
                "name": sha[:7],
                "message": _short(details.get("message"), 4_000),
                "author": _short(author_info.get("name"), 200),
                "date": _short(author_info.get("date"), 60),
                "url": _short(commit.get("html_url"), 500),
                "kind": "commit",
            }
        )
        if user_id:
            # AUTHOR edges are assembled after all people are written.
            author_edges.append(
                {"repository_id": repository_id, "source": user_id, "target": identity}
            )
        for changed in changed_files if isinstance(changed_files, list) else []:
            if (
                not isinstance(changed, dict)
                or _short(changed.get("_commit_sha"), 64) != sha
            ):
                continue
            path = changed.get("filename")
            if isinstance(path, str):
                file_id = f"{repository_id}:file:{path}"
                changes_edges.append(
                    {
                        "repository_id": repository_id,
                        "source": identity,
                        "target": file_id,
                    }
                )

    pull_rows: list[dict[str, Any]] = []
    pull_edges: list[dict[str, str]] = []
    for record in pull_requests if isinstance(pull_requests, list) else []:
        if not isinstance(record, dict):
            continue
        pull = record.get("pull") if isinstance(record.get("pull"), dict) else {}
        number = pull.get("number")
        if not isinstance(number, int) or number <= 0:
            continue
        identity = f"{repository_id}:pr:{number}"
        pull_rows.append(
            {
                "id": identity,
                "repository_id": repository_id,
                "number": number,
                "name": f"PR #{number}",
                "title": _short(pull.get("title"), 500),
                "body": _short(pull.get("body"), 8_000),
                "state": _short(pull.get("state"), 30),
                "merged_at": _short(pull.get("merged_at"), 60),
                "updated_at": _short(pull.get("updated_at"), 60),
                "url": _short(pull.get("html_url"), 500),
                "kind": "pull_request",
            }
        )
        user_id = _user(pull.get("user"), repository_id, person_nodes)
        if user_id:
            author_edges.append(
                {"repository_id": repository_id, "source": user_id, "target": identity}
            )
        files_for_pull = (
            record.get("files") if isinstance(record.get("files"), list) else []
        )
        for changed in files_for_pull:
            if isinstance(changed, dict) and isinstance(changed.get("filename"), str):
                path = changed["filename"]
                file_id = f"{repository_id}:file:{path}"
                if path not in file_by_path:
                    file_by_path[path] = {
                        "id": file_id,
                        "repository_id": repository_id,
                        "name": path.rsplit("/", 1)[-1],
                        "path": path,
                        "kind": "file",
                        "language": _language_from_path(path),
                        "description": "File referenced by pull request history",
                        "historical_only": True,
                    }
                pull_edges.append(
                    {
                        "repository_id": repository_id,
                        "source": identity,
                        "target": file_id,
                    }
                )

    pull_numbers = {
        int(row["number"]): str(row["id"])
        for row in pull_rows
        if isinstance(row.get("number"), int)
    }
    supersedes_edges: list[dict[str, str]] = []
    for pull in pull_rows:
        number = pull.get("number")
        if not isinstance(number, int):
            continue
        for target_number in _explicitly_superseded_prs(
            pull.get("title"), pull.get("body")
        ):
            target = pull_numbers.get(target_number)
            if target and target_number < number:
                supersedes_edges.append(
                    {
                        "repository_id": repository_id,
                        "source": str(pull["id"]),
                        "target": target,
                    }
                )

    issue_rows: list[dict[str, Any]] = []
    issue_edges: list[dict[str, str]] = []
    for issue in issues if isinstance(issues, list) else []:
        if not isinstance(issue, dict) or not isinstance(issue.get("number"), int):
            continue
        number = issue["number"]
        identity = f"{repository_id}:issue:{number}"
        issue_rows.append(
            {
                "id": identity,
                "repository_id": repository_id,
                "number": number,
                "name": f"Issue #{number}",
                "title": _short(issue.get("title"), 500),
                "body": _short(issue.get("body"), 8_000),
                "state": _short(issue.get("state"), 30),
                "updated_at": _short(issue.get("updated_at"), 60),
                "url": _short(issue.get("html_url"), 500),
                "kind": "issue",
            }
        )
        linked_by = issue.get("referenced_by_pull")
        if isinstance(linked_by, int):
            issue_edges.append(
                {
                    "repository_id": repository_id,
                    "source": f"{repository_id}:pr:{linked_by}",
                    "target": identity,
                }
            )

    comment_rows: list[dict[str, Any]] = []
    comment_edges: list[dict[str, str]] = []
    for index, comment in enumerate(comments if isinstance(comments, list) else []):
        if not isinstance(comment, dict):
            continue
        number = comment.get("pull_number")
        body = _short(comment.get("body"), 6_000)
        if not isinstance(number, int) or not body:
            continue
        source_id = _short(comment.get("html_url"), 500) or f"{number}:{index}"
        stable_id = hashlib.sha256(source_id.encode("utf-8")).hexdigest()[:24]
        identity = f"{repository_id}:comment:{stable_id}"
        author = comment.get("user") if isinstance(comment.get("user"), dict) else {}
        user_id = _user(author, repository_id, person_nodes)
        review_state = _short(comment.get("state"), 40)
        kind = (
            "review"
            if review_state
            else (
                "inline_review"
                if isinstance(comment.get("path"), str)
                else "discussion"
            )
        )
        comment_rows.append(
            {
                "id": identity,
                "repository_id": repository_id,
                "name": f"Comment on PR #{number}",
                "body": body,
                "kind": kind,
                "path": _short(comment.get("path"), 500),
                "line": comment.get("line")
                if isinstance(comment.get("line"), int)
                else None,
                "date": _short(
                    comment.get("submitted_at") or comment.get("created_at"), 60
                ),
                "url": source_id,
            }
        )
        comment_edges.append(
            {
                "repository_id": repository_id,
                "source": f"{repository_id}:pr:{number}",
                "target": identity,
            }
        )
        for target_number in _explicitly_superseded_prs(body):
            target = pull_numbers.get(target_number)
            if target and target_number < number:
                supersedes_edges.append(
                    {
                        "repository_id": repository_id,
                        "source": identity,
                        "target": target,
                    }
                )
        if user_id:
            author_edges.append(
                {"repository_id": repository_id, "source": user_id, "target": identity}
            )

    import_edges = [
        {
            "repository_id": repository_id,
            "source": f"{repository_id}:file:{source}",
            "target": f"{repository_id}:file:{target}",
        }
        for source, target in imports
        if isinstance(source, str) and isinstance(target, str)
    ]

    async def write_snapshot(transaction: Any) -> None:
        for label in (
            "Comment",
            "Issue",
            "PullRequest",
            "Commit",
            "Person",
            "File",
            "Directory",
            "Repository",
        ):
            result = await transaction.run(
                f"MATCH (n:{label} {{repository_id: $repository_id}}) DETACH DELETE n",
                repository_id=repository_id,
            )
            await result.consume()
        for label, rows in (
            ("Repository", repo_node),
            ("Directory", directory_rows),
            ("File", list(file_by_path.values())),
            ("Commit", commit_rows),
            ("PullRequest", pull_rows),
            ("Issue", issue_rows),
            ("Comment", comment_rows),
            ("Person", list(person_nodes.values())),
        ):
            await _upsert_nodes(transaction, label, rows)
        for relationship, rows in (
            ("CONTAINS", containment),
            ("IMPORTS", import_edges),
            ("CHANGES", changes_edges),
            ("AUTHORED", author_edges),
            ("MODIFIES", pull_edges),
            ("REFERENCES", issue_edges),
            ("HAS_COMMENT", comment_edges),
            ("SUPERSEDES", supersedes_edges),
        ):
            await _upsert_edges(transaction, relationship, rows)

    async with driver.session(database=database) as session:
        await session.execute_write(write_snapshot)

    return {
        "files": len(file_by_path),
        "directories": len(directory_rows),
        "imports": len(import_edges),
        "commits": len(commit_rows),
        "pull_requests": len(pull_rows),
        "issues": len(issue_rows),
        "discussion_records": len(comment_rows),
        "explicit_supersession_links": len(supersedes_edges),
    }


def _language_from_path(path: str) -> str:
    extension = (
        path.rsplit(".", 1)[-1].lower() if "." in path.rsplit("/", 1)[-1] else ""
    )
    return {
        "js": "JavaScript",
        "jsx": "JavaScript",
        "mjs": "JavaScript",
        "ts": "TypeScript",
        "tsx": "TypeScript",
        "py": "Python",
        "go": "Go",
        "rs": "Rust",
        "java": "Java",
        "cs": "C#",
        "rb": "Ruby",
        "php": "PHP",
        "json": "JSON",
        "toml": "TOML",
        "yaml": "YAML",
        "yml": "YAML",
    }.get(extension, "Other")


async def read_repository_graph(
    driver: AsyncDriver, database: str, repository_id: str, mode: str, limit: int = 350
) -> dict[str, object]:
    labels = (
        ["Repository", "Directory", "File"]
        if mode == "architecture"
        else ["File", "Commit", "PullRequest", "Issue", "Comment", "Person"]
    )
    node_query = (
        "MATCH (n) WHERE n.repository_id = $repository_id AND any(label IN labels(n) WHERE label IN $labels) "
        "WITH n, labels(n) AS node_labels, properties(n) AS props "
        "ORDER BY CASE "
        "WHEN 'Repository' IN node_labels THEN 0 "
        "WHEN $mode = 'architecture' AND 'Directory' IN node_labels THEN 1 "
        "WHEN $mode = 'architecture' AND 'File' IN node_labels THEN 2 "
        "WHEN $mode = 'decisions' AND 'PullRequest' IN node_labels THEN 0 "
        "WHEN $mode = 'decisions' AND 'Commit' IN node_labels THEN 1 "
        "WHEN $mode = 'decisions' AND 'Issue' IN node_labels THEN 2 "
        "WHEN $mode = 'decisions' AND 'Comment' IN node_labels THEN 3 "
        "WHEN $mode = 'decisions' AND 'File' IN node_labels THEN 4 "
        "ELSE 5 END, props['number'] DESC, props['date'] DESC, "
        "props['updated_at'] DESC, props['path'] LIMIT $limit "
        "RETURN node_labels AS labels, props"
    )
    records, _, _ = await driver.execute_query(
        node_query,
        repository_id=repository_id,
        labels=labels,
        mode=mode,
        limit=limit,
        database_=database,
    )
    nodes: list[dict[str, object]] = []
    ids: list[str] = []
    repo_meta: dict[str, object] = {}
    for record in records:
        props = dict(record["props"])
        identity = str(props.get("id", ""))
        node_labels = record["labels"]
        kind = str(
            props.get("kind")
            or next(
                (label.lower() for label in node_labels if label != "Repository"),
                "repository",
            )
        )
        node = {
            "id": identity,
            "label": str(
                props.get("title") or props.get("name") or props.get("path") or identity
            ),
            "kind": kind,
            "path": props.get("path"),
            "description": props.get("description")
            or props.get("body")
            or props.get("message"),
            "metadata": {
                key: value
                for key, value in props.items()
                if isinstance(value, (str, int, bool))
                and key
                not in {
                    "id",
                    "repository_id",
                    "name",
                    "title",
                    "description",
                    "body",
                    "message",
                    "path",
                }
            },
        }
        nodes.append(node)
        ids.append(identity)
        if "Repository" in node_labels:
            repo_meta = {
                key: props.get(key)
                for key in (
                    "name",
                    "owner",
                    "url",
                    "description",
                    "default_branch",
                    "stars",
                    "indexed_at",
                    "tree_truncated",
                )
            }

    edge_query = (
        "MATCH (a {repository_id: $repository_id})-[relationship]->(b {repository_id: $repository_id}) "
        "WHERE a.id IN $ids AND b.id IN $ids "
        "RETURN a.id AS source, b.id AS target, type(relationship) AS kind LIMIT $edge_limit"
    )
    edges_result, _, _ = await driver.execute_query(
        edge_query,
        repository_id=repository_id,
        ids=ids,
        edge_limit=900,
        database_=database,
    )
    edges = [
        {
            "id": f"{item['source']}:{item['kind']}:{item['target']}",
            "source": item["source"],
            "target": item["target"],
            "kind": item["kind"],
            "label": item["kind"].replace("_", " ").lower(),
        }
        for item in edges_result
    ]
    coverage_query = "MATCH (n {repository_id: $repository_id}) RETURN labels(n) AS labels, count(n) AS amount"
    coverage_records, _, _ = await driver.execute_query(
        coverage_query, repository_id=repository_id, database_=database
    )
    coverage: dict[str, int | bool | str] = {"repository": repository_id}
    for item in coverage_records:
        for label in item["labels"]:
            coverage[label.lower()] = int(item["amount"])
    coverage["explicit_supersession_links"] = sum(
        1 for edge in edges if edge["kind"] == "SUPERSEDES"
    )
    return {
        "repository": repo_meta,
        "nodes": nodes,
        "edges": edges,
        "coverage": coverage,
    }


async def delete_repository_graph(
    driver: AsyncDriver, database: str, repository_id: str
) -> None:
    labels = (
        "Comment",
        "Issue",
        "PullRequest",
        "Commit",
        "Person",
        "File",
        "Directory",
        "Repository",
    )

    async def delete_snapshot(transaction: Any) -> None:
        for label in labels:
            result = await transaction.run(
                f"MATCH (n:{label} {{repository_id: $repository_id}}) DETACH DELETE n",
                repository_id=repository_id,
            )
            await result.consume()

    async with driver.session(database=database) as session:
        await session.execute_write(delete_snapshot)


async def list_workspace_repositories(
    driver: AsyncDriver, database: str, workspace_id: str, user_id: str | None = None
) -> list[dict[str, str | None]]:
    rows, _, _ = await driver.execute_query(
        "MATCH (r:Repository) WHERE "
        "(r.repository_id STARTS WITH $prefix AND r.expires_at > $now) "
        "OR ($user_id IS NOT NULL AND r.is_saved = true AND r.owner_user_id = $user_id) "
        "RETURN r.owner AS owner, r.name AS full_name, r.description AS description, "
        "r.default_branch AS default_branch, r.indexed_at AS indexed_at, r.is_saved AS is_saved "
        "ORDER BY r.indexed_at DESC LIMIT 50",
        prefix=f"{workspace_id}:github.com/",
        user_id=user_id,
        now=datetime.now(UTC).isoformat(),
        database_=database,
    )
    repositories: list[dict[str, str | None]] = []
    for row in rows:
        full_name = str(row.get("full_name") or "")
        owner = str(row.get("owner") or "")
        repository = full_name.split("/", 1)[1] if "/" in full_name else ""
        if not owner or not repository:
            continue
        repositories.append(
            {
                "owner": owner,
                "repository": repository,
                "full_name": full_name,
                "description": row.get("description"),
                "default_branch": str(row.get("default_branch") or "main"),
                "indexed_at": str(row.get("indexed_at") or ""),
                "is_saved": bool(row.get("is_saved")),
            }
        )
    return repositories


async def touch_repository_graph(
    driver: AsyncDriver, database: str, repository_id: str, user_id: str | None = None
) -> bool:
    expires_at = (datetime.now(UTC) + TEMPORARY_GRAPH_TTL).isoformat()
    rows, _, _ = await driver.execute_query(
        "MATCH (r:Repository {id: $id}) WHERE r.expires_at > $now OR "
        "(r.is_saved = true AND $user_id IS NOT NULL AND r.owner_user_id = $user_id) "
        "WITH r, CASE WHEN r.is_saved = true THEN null ELSE $expires_at END AS expiry "
        "SET r.expires_at = expiry "
        "RETURN r.id AS id LIMIT 1",
        id=repository_id,
        now=datetime.now(UTC).isoformat(),
        expires_at=expires_at,
        user_id=user_id,
        database_=database,
    )
    return bool(rows)


async def find_accessible_repository_id(
    driver: AsyncDriver,
    database: str,
    workspace_id: str,
    user_id: str | None,
    full_name: str,
) -> str | None:
    rows, _, _ = await driver.execute_query(
        "MATCH (r:Repository) WHERE toLower(r.name) = $full_name AND ("
        "(r.repository_id STARTS WITH $prefix AND r.expires_at > $now) OR "
        "($user_id IS NOT NULL AND r.is_saved = true AND r.owner_user_id = $user_id)) "
        "RETURN r.id AS id LIMIT 1",
        full_name=full_name.lower(),
        prefix=f"{workspace_id}:github.com/",
        user_id=user_id,
        now=datetime.now(UTC).isoformat(),
        database_=database,
    )
    return str(rows[0]["id"]) if rows else None


async def find_saved_repository_id(
    driver: AsyncDriver, database: str, user_id: str, full_name: str
) -> str | None:
    rows, _, _ = await driver.execute_query(
        "MATCH (r:Repository) WHERE r.is_saved = true AND r.owner_user_id = $user_id "
        "AND toLower(r.name) = $full_name RETURN r.id AS id LIMIT 1",
        user_id=user_id,
        full_name=full_name.lower(),
        database_=database,
    )
    return str(rows[0]["id"]) if rows else None


async def save_repository_graph(
    driver: AsyncDriver,
    database: str,
    repository_id: str,
    workspace_id: str,
    user_id: str,
) -> bool:
    rows, _, _ = await driver.execute_query(
        "MATCH (r:Repository {id: $id}) "
        "WHERE r.repository_id STARTS WITH $prefix AND r.expires_at > $now "
        "SET r.owner_user_id = $user_id, r.is_saved = true, r.expires_at = null "
        "RETURN r.id AS id LIMIT 1",
        id=repository_id,
        prefix=f"{workspace_id}:github.com/",
        now=datetime.now(UTC).isoformat(),
        user_id=user_id,
        database_=database,
    )
    return bool(rows)


async def delete_expired_repository_graphs(
    driver: AsyncDriver, database: str, limit: int = 100
) -> int:
    now = datetime.now(UTC).isoformat()
    rows, _, _ = await driver.execute_query(
        "MATCH (r:Repository) WHERE r.repository_id CONTAINS ':github.com/' "
        "AND coalesce(r.is_saved, false) = false "
        "AND (r.expires_at IS NULL OR r.expires_at <= $now) "
        "RETURN r.id AS id LIMIT $limit",
        now=now,
        limit=limit,
        database_=database,
    )
    expired_ids = [str(row["id"]) for row in rows]
    for repository_id in expired_ids:
        await delete_repository_graph(driver, database, repository_id)
    return len(expired_ids)


async def delete_workspace_repositories(
    driver: AsyncDriver, database: str, workspace_id: str
) -> int:
    rows, _, _ = await driver.execute_query(
        "MATCH (r:Repository) WHERE r.repository_id STARTS WITH $prefix "
        "AND coalesce(r.is_saved, false) = false "
        "RETURN r.id AS id LIMIT 100",
        prefix=f"{workspace_id}:github.com/",
        database_=database,
    )
    repository_ids = [str(row["id"]) for row in rows]
    for repository_id in repository_ids:
        await delete_repository_graph(driver, database, repository_id)
    return len(repository_ids)
