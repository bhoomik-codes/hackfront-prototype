from typing import Any

from neo4j import AsyncDriver


async def ensure_account_schema(driver: AsyncDriver, database: str) -> None:
    async with driver.session(database=database) as session:
        for statement in (
            "CREATE CONSTRAINT account_id IF NOT EXISTS FOR (n:Account) REQUIRE n.id IS UNIQUE",
            "CREATE CONSTRAINT pending_signup_id IF NOT EXISTS FOR (n:PendingSignup) REQUIRE n.id IS UNIQUE",
        ):
            result = await session.run(statement)
            await result.consume()


async def get_account(
    driver: AsyncDriver, database: str, account_id: str
) -> dict[str, Any] | None:
    rows, _, _ = await driver.execute_query(
        "MATCH (a:Account {id: $id}) RETURN a.id AS id, a.email AS email, "
        "a.password_hash AS password_hash, a.email_verified AS email_verified, "
        "a.two_factor_enabled AS two_factor_enabled, "
        "a.two_factor_digest AS two_factor_digest, "
        "a.two_factor_expires AS two_factor_expires, "
        "a.two_factor_attempts AS two_factor_attempts, "
        "a.two_factor_workspace AS two_factor_workspace, "
        "a.two_factor_enrollment_digest AS two_factor_enrollment_digest, "
        "a.two_factor_enrollment_expires AS two_factor_enrollment_expires, "
        "a.two_factor_enrollment_attempts AS two_factor_enrollment_attempts, "
        "a.two_factor_enrollment_workspace AS two_factor_enrollment_workspace",
        id=account_id,
        database_=database,
    )
    return dict(rows[0]) if rows else None


async def create_pending_signup_if_absent(
    driver: AsyncDriver,
    database: str,
    account_id: str,
    email: str,
    password_hash: str,
    code_digest: str,
    expires_at: float,
    workspace_id: str,
    now: float,
) -> bool:
    """Create pending signup only if the address has no completed account."""
    await ensure_account_schema(driver, database)

    async def write(tx):
        result = await tx.run(
            "OPTIONAL MATCH (a:Account {id: $id}) "
            "WITH a WHERE a IS NULL "
            "MERGE (p:PendingSignup {id: $id}) "
            "SET p.email = $email, p.password_hash = $password_hash, "
            "p.code_digest = $code_digest, p.expires_at = $expires_at, "
            "p.workspace_id = $workspace_id, p.attempts = 0, p.created_at = $now "
            "RETURN p.id AS id",
            id=account_id,
            email=email,
            password_hash=password_hash,
            code_digest=code_digest,
            expires_at=expires_at,
            workspace_id=workspace_id,
            now=now,
        )
        return await result.single()

    async with driver.session(database=database) as session:
        row = await session.execute_write(write)
    return row is not None


async def get_pending_signup(
    driver: AsyncDriver, database: str, account_id: str
) -> dict[str, Any] | None:
    rows, _, _ = await driver.execute_query(
        "MATCH (p:PendingSignup {id: $id}) RETURN p.email AS email, "
        "p.password_hash AS password_hash, p.code_digest AS code_digest, "
        "p.expires_at AS expires_at, p.workspace_id AS workspace_id, "
        "p.attempts AS attempts",
        id=account_id,
        database_=database,
    )
    return dict(rows[0]) if rows else None


async def has_active_auth_challenge(
    driver: AsyncDriver, database: str, workspace_id: str, now: float
) -> bool:
    signup_rows, _, _ = await driver.execute_query(
        "MATCH (p:PendingSignup {workspace_id: $workspace_id}) "
        "WHERE p.expires_at > $now RETURN p.id AS id LIMIT 1",
        workspace_id=workspace_id,
        now=now,
        database_=database,
    )
    if signup_rows:
        return True
    login_rows, _, _ = await driver.execute_query(
        "MATCH (a:Account {two_factor_workspace: $workspace_id}) "
        "WHERE a.two_factor_expires > $now RETURN a.id AS id LIMIT 1",
        workspace_id=workspace_id,
        now=now,
        database_=database,
    )
    return bool(login_rows)


async def increment_pending_signup_attempts(
    driver: AsyncDriver, database: str, account_id: str
) -> None:
    await driver.execute_query(
        "MATCH (p:PendingSignup {id: $id}) SET p.attempts = coalesce(p.attempts, 0) + 1 "
        "WITH p WHERE p.attempts >= 5 DETACH DELETE p",
        id=account_id,
        database_=database,
    )


async def delete_pending_signup(
    driver: AsyncDriver, database: str, account_id: str
) -> None:
    await driver.execute_query(
        "MATCH (p:PendingSignup {id: $id}) DETACH DELETE p",
        id=account_id,
        database_=database,
    )


async def complete_pending_signup(
    driver: AsyncDriver,
    database: str,
    account_id: str,
    email: str,
    password_hash: str,
    now: float,
) -> bool:
    async def write(tx):
        result = await tx.run(
            "OPTIONAL MATCH (a:Account {id: $id}) "
            "WITH a WHERE a IS NULL "
            "CREATE (a:Account {id: $id, email: $email, password_hash: $password_hash, "
            "email_verified: true, two_factor_enabled: false, two_factor_digest: '', "
            "two_factor_expires: 0, two_factor_workspace: '', two_factor_attempts: 0, "
            "created_at: $now}) "
            "RETURN a.id AS id",
            id=account_id,
            email=email,
            password_hash=password_hash,
            now=now,
        )
        row = await result.single()
        await tx.run("MATCH (p:PendingSignup {id: $id}) DETACH DELETE p", id=account_id)
        return row is not None

    async with driver.session(database=database) as session:
        return await session.execute_write(write)


async def update_two_factor_challenge(
    driver: AsyncDriver,
    database: str,
    account_id: str,
    digest: str,
    expires_at: float,
    workspace_id: str,
) -> None:
    await driver.execute_query(
        "MATCH (a:Account {id: $id, email_verified: true, two_factor_enabled: true}) "
        "SET a.two_factor_digest = $digest, a.two_factor_expires = $expires_at, "
        "a.two_factor_workspace = $workspace_id, a.two_factor_attempts = 0 "
        "RETURN a.id AS id",
        id=account_id,
        digest=digest,
        expires_at=expires_at,
        workspace_id=workspace_id,
        database_=database,
    )


async def prepare_two_factor_enrollment(
    driver: AsyncDriver,
    database: str,
    account_id: str,
    digest: str,
    expires_at: float,
    workspace_id: str,
) -> bool:
    rows, _, _ = await driver.execute_query(
        "MATCH (a:Account {id: $id, email_verified: true, two_factor_enabled: false}) "
        "SET a.two_factor_enrollment_digest = $digest, "
        "a.two_factor_enrollment_expires = $expires_at, "
        "a.two_factor_enrollment_workspace = $workspace_id, "
        "a.two_factor_enrollment_attempts = 0 "
        "RETURN a.id AS id",
        id=account_id,
        digest=digest,
        expires_at=expires_at,
        workspace_id=workspace_id,
        database_=database,
    )
    return bool(rows)


async def increment_two_factor_enrollment_attempts(
    driver: AsyncDriver, database: str, account_id: str
) -> None:
    await driver.execute_query(
        "MATCH (a:Account {id: $id}) SET a.two_factor_enrollment_attempts = "
        "coalesce(a.two_factor_enrollment_attempts, 0) + 1 "
        "WITH a WHERE a.two_factor_enrollment_attempts >= 5 REMOVE "
        "a.two_factor_enrollment_digest, a.two_factor_enrollment_expires, "
        "a.two_factor_enrollment_workspace, a.two_factor_enrollment_attempts",
        id=account_id,
        database_=database,
    )


async def clear_two_factor_enrollment(
    driver: AsyncDriver, database: str, account_id: str
) -> None:
    await driver.execute_query(
        "MATCH (a:Account {id: $id}) REMOVE a.two_factor_enrollment_digest, "
        "a.two_factor_enrollment_expires, a.two_factor_enrollment_workspace, "
        "a.two_factor_enrollment_attempts",
        id=account_id,
        database_=database,
    )


async def confirm_two_factor_enrollment(
    driver: AsyncDriver,
    database: str,
    account_id: str,
    workspace_id: str,
    now: float,
) -> bool:
    rows, _, _ = await driver.execute_query(
        "MATCH (a:Account {id: $id, email_verified: true, two_factor_enabled: false, "
        "two_factor_enrollment_workspace: $workspace_id}) "
        "WHERE a.two_factor_enrollment_expires > $now "
        "AND a.two_factor_enrollment_attempts < 5 "
        "SET a.two_factor_enabled = true "
        "REMOVE a.two_factor_enrollment_digest, a.two_factor_enrollment_expires, "
        "a.two_factor_enrollment_workspace, a.two_factor_enrollment_attempts "
        "RETURN a.id AS id",
        id=account_id,
        workspace_id=workspace_id,
        now=now,
        database_=database,
    )
    return bool(rows)


async def increment_two_factor_attempts(
    driver: AsyncDriver, database: str, account_id: str
) -> None:
    await driver.execute_query(
        "MATCH (a:Account {id: $id}) SET a.two_factor_attempts = coalesce(a.two_factor_attempts, 0) + 1 "
        "WITH a WHERE a.two_factor_attempts >= 5 REMOVE a.two_factor_digest, "
        "a.two_factor_expires, a.two_factor_workspace, a.two_factor_attempts",
        id=account_id,
        database_=database,
    )


async def consume_two_factor_challenge(
    driver: AsyncDriver, database: str, account_id: str
) -> None:
    await driver.execute_query(
        "MATCH (a:Account {id: $id}) REMOVE a.two_factor_digest, "
        "a.two_factor_expires, a.two_factor_workspace, a.two_factor_attempts",
        id=account_id,
        database_=database,
    )


async def clear_expired_account_challenges(
    driver: AsyncDriver, database: str, now: float
) -> int:
    rows, _, _ = await driver.execute_query(
        "MATCH (p:PendingSignup) WHERE p.expires_at <= $now "
        "WITH collect(p) AS expired "
        "FOREACH (node IN expired | DELETE node) "
        "RETURN size(expired) AS amount",
        now=now,
        database_=database,
    )
    expired_signups = int(rows[0]["amount"]) if rows else 0
    await driver.execute_query(
        "MATCH (a:Account) WHERE a.two_factor_expires IS NOT NULL "
        "AND a.two_factor_expires <= $now REMOVE a.two_factor_digest, "
        "a.two_factor_expires, a.two_factor_workspace, a.two_factor_attempts",
        now=now,
        database_=database,
    )
    await driver.execute_query(
        "MATCH (a:Account) WHERE a.two_factor_enrollment_expires IS NOT NULL "
        "AND a.two_factor_enrollment_expires <= $now REMOVE "
        "a.two_factor_enrollment_digest, a.two_factor_enrollment_expires, "
        "a.two_factor_enrollment_workspace, a.two_factor_enrollment_attempts",
        now=now,
        database_=database,
    )
    return expired_signups


async def set_two_factor_enabled(
    driver: AsyncDriver, database: str, account_id: str, enabled: bool
) -> bool:
    rows, _, _ = await driver.execute_query(
        "MATCH (a:Account {id: $id, email_verified: true}) "
        "SET a.two_factor_enabled = $enabled "
        "REMOVE a.two_factor_digest, a.two_factor_expires, "
        "a.two_factor_workspace, a.two_factor_attempts "
        "RETURN a.id AS id",
        id=account_id,
        enabled=enabled,
        database_=database,
    )
    return bool(rows)
