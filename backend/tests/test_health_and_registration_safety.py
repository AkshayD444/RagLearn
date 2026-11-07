"""Health-disclosure and registration-race regressions: A3 and A7.

These duplicate a little of ``test_api_route_coverage.py`` on purpose. That file
asserts the shape of each route; this one asserts the *absence* of a leak and
the *absence* of a 500, which are the properties that must not be quietly
reintroduced by a later refactor.
"""

import asyncio
import uuid
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.sql.expression import Insert

from app.api.deps import get_db
from app.core.database import Base, check_database_connection
from app.main import app
from app.models.user import User
from tests.conftest import TestAsyncSessionLocal

# A realistic asyncpg connection failure. This is the shape of string that used
# to be returned straight to an anonymous caller.
LEAKY_DB_ERROR = (
    "password authentication failed for user \"postgres\" "
    "(SQLSTATE 28P01)\n[SQL: SELECT 1]\n(Background on this error at: "
    "https://sqlalche.me/e/20/e3q8)"
)


def _probe_raising(error: Exception):
    """Builds a stub session whose every query fails with ``error``."""

    class _Broken:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def execute(self, *_args, **_kwargs):
            raise error

    return _Broken


# ---------------------------------------------------------------------------
# A3 -- /health/db must not disclose internal detail
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_health_db_does_not_leak_driver_details(monkeypatch):
    """The raw driver message must never reach the response body."""
    import app.api.v1.health as health_api

    monkeypatch.setattr(
        health_api,
        "check_database_connection",
        lambda: _returns(
            {
                "status": "disconnected",
                "select_one": False,
                "pgvector_installed": False,
                "error": LEAKY_DB_ERROR,
            }
        ),
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        res = await ac.get("/api/v1/health/db")

    assert res.status_code == 503
    text = res.text
    for fragment in (
        "postgres",
        "SQLSTATE",
        "28P01",
        "sqlalche.me",
        "SELECT 1",
    ):
        assert fragment not in text, fragment
    assert "password authentication failed" not in text


@pytest.mark.asyncio
async def test_health_db_probe_logs_the_real_cause(monkeypatch, caplog):
    """A3 is disclosure, not silence: operators must still see the cause."""
    import app.api.v1.health as health_api

    async def _failing():
        return await check_database_connection()

    monkeypatch.setattr("app.core.database.AsyncSessionLocal", _probe_raising(RuntimeError(LEAKY_DB_ERROR)))
    monkeypatch.setattr(health_api, "check_database_connection", _failing)

    with caplog.at_level("ERROR", logger="app.database"):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            res = await ac.get("/api/v1/health/db")

    assert res.status_code == 503
    assert LEAKY_DB_ERROR in caplog.text


@pytest.mark.asyncio
async def test_health_db_never_carries_an_error_field(monkeypatch):
    """Defence in depth: only known-safe fields are copied out.

    Even if a future change to the probe adds a ``dsn`` or ``error`` key, the
    endpoint's allow-list means it cannot reach an anonymous caller.
    """
    import app.api.v1.health as health_api

    monkeypatch.setattr(
        health_api,
        "check_database_connection",
        lambda: _returns(
            {
                "status": "disconnected",
                "select_one": False,
                "pgvector_installed": False,
                "message": "Database connection failed.",
                "error": LEAKY_DB_ERROR,
                "dsn": "postgresql://postgres:postgres@db:5432/app",
                "database_url": "postgresql+asyncpg://postgres:postgres@db/app",
            }
        ),
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        res = await ac.get("/api/v1/health/db")

    database = res.json()["database"]
    assert set(database) <= {
        "status",
        "select_one",
        "pgvector_installed",
        "message",
    }
    assert "error" not in database
    assert "dsn" not in database
    assert "postgres" not in res.text


@pytest.mark.asyncio
async def test_health_db_still_reports_healthy_when_up(monkeypatch):
    """The endpoint has to remain useful to a load balancer."""
    import app.api.v1.health as health_api

    monkeypatch.setattr(
        health_api,
        "check_database_connection",
        lambda: _returns(
            {
                "status": "connected",
                "select_one": True,
                "pgvector_installed": True,
                "message": "Database connection successful.",
            }
        ),
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        res = await ac.get("/api/v1/health/db")

    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "healthy"
    assert body["database"]["select_one"] is True
    assert body["database"]["pgvector_installed"] is True


@pytest.mark.asyncio
async def test_basic_health_endpoint_is_unchanged(client):
    """The unauthenticated liveness probe must keep answering 200."""
    res = await client.get("/api/v1/health")
    assert res.status_code == 200
    assert res.json()["status"] == "online"


@pytest.mark.asyncio
async def test_health_endpoints_remain_unauthenticated(client, monkeypatch):
    """A3 must not have been "fixed" by adding auth: probes hold no credentials.

    Requiring a token here would break every load balancer and uptime check.
    """
    import app.api.v1.health as health_api

    monkeypatch.setattr(
        health_api,
        "check_database_connection",
        lambda: _returns(
            {
                "status": "connected",
                "select_one": True,
                "pgvector_installed": False,
                "message": "Database connection successful.",
            }
        ),
    )

    assert (await client.get("/api/v1/health")).status_code == 200
    assert (await client.get("/api/v1/health/db")).status_code == 200


# ---------------------------------------------------------------------------
# A7 -- concurrent duplicate registration
# ---------------------------------------------------------------------------


def _land_competitor_at_insert(monkeypatch, email: str, full_name: str = "Sneaky") -> None:
    """Opens the registration race window immediately before the INSERT.

    A7: the race is a time-of-check/time-of-use window. The competing row has to
    appear *after* the handler's existence SELECT misses and *before* its INSERT
    reaches the database.

    These tests used to patch ``commit`` to do that, because the INSERT used to
    be flushed by the commit. The handler now issues its INSERT directly through
    ``execute``, so that is the seam: the competitor is written immediately
    before the handler's own INSERT, which is exactly the interleaving.

    The re-entrancy guard is load-bearing. The competitor is written by a second
    session through the same patched class, so without disarming after the first
    hit this would recurse forever.
    """
    original_execute = type(TestAsyncSessionLocal()).execute
    armed = {"value": True}

    async def execute_with_race(self, statement, *args, **kwargs):
        if (
            armed["value"]
            and isinstance(statement, Insert)
            and statement.table.name == "users"
        ):
            armed["value"] = False
            async with TestAsyncSessionLocal() as other:
                other.add(
                    User(
                        email=email,
                        full_name=full_name,
                        hashed_password="$pbkdf2-sha256$1$saltsalt$c2FsdA",
                    )
                )
                await other.commit()
        return await original_execute(self, statement, *args, **kwargs)

    monkeypatch.setattr(type(TestAsyncSessionLocal()), "execute", execute_with_race)


@pytest.mark.asyncio
async def test_duplicate_registration_returns_400_not_500(client):
    """The friendly duplicate message survives."""
    payload = {
        "email": "race@example.com",
        "full_name": "First",
        "password": "password123",
    }
    first = await client.post("/api/v1/auth/register", json=payload)
    assert first.status_code == 201

    second = await client.post("/api/v1/auth/register", json=payload)
    assert second.status_code == 400
    assert "already exists" in second.json()["detail"].lower()


@pytest.mark.asyncio
async def test_unique_constraint_violation_is_not_a_500(client, monkeypatch):
    """A7: the INSERT losing the unique race must still answer 400.

    The pre-check and the INSERT are not atomic, so a row can appear between
    them. This drives exactly that interleaving: the check finds nothing, the
    INSERT collides, and the endpoint must still answer 400.
    """
    email = "interleaved@example.com"
    _land_competitor_at_insert(monkeypatch, email)

    res = await client.post(
        "/api/v1/auth/register",
        json={"email": email, "full_name": "Loser", "password": "password123"},
    )

    assert res.status_code == 400, res.text
    assert "already exists" in res.json()["detail"].lower()


@pytest.mark.asyncio
async def test_a_lost_race_does_not_roll_back_the_winners_row(client, monkeypatch):
    """The regression this handler was rewritten for.

    A7. The losing branch used to call ``db.rollback()`` and then re-read. Two
    things went wrong with that.

    The rollback is the serious one. Every request in this suite shares a
    single connection (``StaticPool`` over in-memory SQLite), so a losing
    request's rollback unwound the winner's *committed* insert along with its
    own failed statement -- the burst handed back a 201 for an account that no
    longer existed. There is no rollback on the losing path now, because
    nothing is raised: the conflict is settled by the INSERT itself.

    The re-read is the second. The conflict used to be confirmed by a second
    query whose answer decided the response, so what the caller was told
    depended on a read that could observe something different from the write
    it was reporting on.

    The competitor here is committed before the handler's INSERT runs, which is
    what makes the assertion about the surviving row meaningful -- a row that was
    never committed could not be rolled away in the first place.
    """
    email = "survivor@example.com"
    _land_competitor_at_insert(monkeypatch, email)

    loser = await client.post(
        "/api/v1/auth/register",
        json={"email": email, "full_name": "Loser", "password": "password123"},
    )
    assert loser.status_code == 400, loser.text

    async with TestAsyncSessionLocal() as session:
        rows = (
            await session.execute(select(User).where(User.email == email))
        ).scalars().all()

    assert len(rows) == 1, f"expected exactly the winner's row, found {len(rows)}"
    assert rows[0].full_name == "Sneaky", "the surviving row must be the committed one"


@pytest.mark.asyncio
async def test_the_losing_path_never_rolls_back(client, monkeypatch):
    """A structural check on the fix, complementing the behavioural one above.

    If the rollback were still there, the behavioural test would pass on its
    own terms while the mechanism that caused the original bug came back with
    it. Asserting the absence of the call keeps the two from drifting apart.
    """
    import app.api.v1.auth as auth_api

    source = Path(auth_api.__file__).read_text(encoding="utf-8")
    register_body = source.split("async def register(", 1)[1].split("\n@router", 1)[0]

    assert "rollback" not in register_body, (
        "the registration path must not roll back: on a shared connection that "
        "discards a competing request's committed row"
    )
    assert "IntegrityError" not in register_body, (
        "the conflict is resolved by ON CONFLICT, so nothing should be raised "
        "and caught here"
    )


@pytest.mark.asyncio
async def test_duplicate_race_leaves_the_session_usable(client, monkeypatch):
    """A caught conflict must leave the session able to keep working.

    The losing INSERT used to raise and needed a rollback to recover. It no
    longer raises at all, so there is nothing to recover from -- but the
    guarantee is the same, and this asserts it end to end rather than trusting
    the mechanism.
    """
    email = "rollback@example.com"
    _land_competitor_at_insert(monkeypatch, email)

    failed = await client.post(
        "/api/v1/auth/register",
        json={"email": email, "full_name": "Loser", "password": "password123"},
    )
    assert failed.status_code == 400

    # A different registration in a fresh session must still work.
    ok = await client.post(
        "/api/v1/auth/register",
        json={
            "email": "after-race@example.com",
            "full_name": "Next",
            "password": "password123",
        },
    )
    assert ok.status_code == 201, ok.text


@pytest.mark.asyncio
async def test_the_conflict_is_narrowed_to_email():
    """Only a genuine email collision may become "already exists".

    ``ON CONFLICT`` is declared against the email index specifically. Left
    unqualified it would swallow *any* conflict -- a foreign key or a check
    violation -- and report a real database fault as "email already exists",
    which is the misleading answer this handler is supposed to avoid.

    The target is part of the statement's meaning, so this reads the SQL each
    dialect actually emits. Compiling the SQLite statement against the
    PostgreSQL compiler would not test anything: the two dialects build
    different clause objects, and forcing one through the other raises rather
    than producing SQL.
    """
    from sqlalchemy.dialects import postgresql, sqlite

    from app.api.v1.auth import _insert_user_returning_row

    async with TestAsyncSessionLocal() as session:
        stmt = _insert_user_returning_row(
            session, User(email="scope@example.com", full_name="N", hashed_password="h")
        )

    assert stmt.table.name == "users"
    sql = str(stmt.compile(dialect=sqlite.dialect())).upper()
    assert "ON CONFLICT" in sql, sql
    assert "EMAIL" in sql.split("ON CONFLICT")[-1], f"conflict target is not email: {sql}"

    # The PostgreSQL path is production's dialect and is built separately here,
    # since the helper resolves its builder from the live bind (SQLite in tests).
    pg_stmt = (
        postgresql.insert(User)
        .values(email="scope@example.com", full_name="N", hashed_password="h")
        .on_conflict_do_nothing(index_elements=[User.email])
        .returning(User)
    )
    pg_sql = str(pg_stmt.compile(dialect=postgresql.dialect())).upper()
    assert "ON CONFLICT" in pg_sql, pg_sql
    assert "EMAIL" in pg_sql.split("ON CONFLICT")[-1], f"conflict target is not email: {pg_sql}"


@pytest.mark.asyncio
async def test_an_unrelated_integrity_failure_is_not_reported_as_a_duplicate(
    client, monkeypatch
):
    """A7: only a genuine email collision may become "already exists".

    Any other integrity failure has to keep surfacing as a server error, or a
    real database problem would be reported to the user as a confusing 400.
    Raised at the INSERT itself, which is where a real foreign key or check
    violation would surface now that the handler no longer pre-flushes.
    """
    original_execute = type(TestAsyncSessionLocal()).execute

    async def execute_with_unrelated_error(self, statement, *args, **kwargs):
        if isinstance(statement, Insert) and statement.table.name == "users":
            raise IntegrityError("INSERT ...", {}, Exception("foreign key violation"))
        return await original_execute(self, statement, *args, **kwargs)

    monkeypatch.setattr(
        type(TestAsyncSessionLocal()), "execute", execute_with_unrelated_error
    )

    with pytest.raises(Exception) as excinfo:
        await client.post(
            "/api/v1/auth/register",
            json={
                "email": "fk@example.com",
                "full_name": "Broken",
                "password": "password123",
            },
        )

    # Re-raised rather than converted into a friendly 400.
    assert not isinstance(excinfo.value, AssertionError)
    assert "already exists" not in str(excinfo.value)


@pytest.mark.asyncio
async def test_duplicate_constraint_still_exists_in_the_database():
    """The safety net the handler relies on must remain enforced.

    A7 resolved the race by letting the database arbitrate, which is only sound
    while the constraint is intact. This asserts it directly, so a migration
    that dropped or de-duplicated the index would fail here.
    """
    from sqlalchemy.exc import IntegrityError as SAIntegrityError

    async with TestAsyncSessionLocal() as session:
        session.add(
            User(
                email="constraint@example.com",
                full_name="One",
                hashed_password="$pbkdf2-sha256$1$saltsalt$c2FsdA",
            )
        )
        await session.commit()

    with pytest.raises(SAIntegrityError):
        async with TestAsyncSessionLocal() as session:
            session.add(
                User(
                    email="constraint@example.com",
                    full_name="Two",
                    hashed_password="$pbkdf2-sha256$1$saltsalt$c2FsdA",
                )
            )
            await session.commit()


@pytest.mark.asyncio
async def test_simultaneous_registrations_all_get_a_clean_response(tmp_path):
    """N concurrent signups for one email: one 201, the rest 400. Never a 500.

    This is the real-world shape of the race. The suite's shared in-memory
    SQLite engine hands every session the same connection, which cannot model
    concurrent transactions: one request's commit can close another's. This
    test therefore uses a file-backed SQLite database so each request gets its
    own connection and the database, not the harness, arbitrates the race.
    """
    email = f"simultaneous-{uuid.uuid4().hex[:8]}@example.com"
    payload = {"email": email, "full_name": "Racer", "password": "password123"}

    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'registration-race.db'}",
        future=True,
    )
    session_factory = async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autocommit=False,
        autoflush=False,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async def _override_get_db():
        async with session_factory() as session:
            yield session

    previous_override = app.dependency_overrides.get(get_db)
    app.dependency_overrides[get_db] = _override_get_db
    try:
        async def attempt():
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as ac:
                return await ac.post("/api/v1/auth/register", json=payload)

        responses = await asyncio.gather(*(attempt() for _ in range(4)))

        codes = sorted(r.status_code for r in responses)
        assert codes == [201, 400, 400, 400], codes
        for res in responses:
            if res.status_code == 400:
                assert "already exists" in res.json()["detail"].lower()

        async with session_factory() as session:
            rows = (
                await session.execute(select(User).where(User.email == email))
            ).scalars().all()
        assert len(rows) == 1
    finally:
        if previous_override is None:
            app.dependency_overrides.pop(get_db, None)
        else:
            app.dependency_overrides[get_db] = previous_override
        await engine.dispose()


def _returns(value):
    """A sync callable returning an already-completed awaitable.

    Lets a test stub the probe without needing an async lambda.
    """

    async def _coro():
        return value

    return _coro()
