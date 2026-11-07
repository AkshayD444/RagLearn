"""The Alembic migrations executed against a real PostgreSQL + pgvector server.

Why this module exists
----------------------
``tests/test_migrations.py`` runs the real Alembic runner, but against SQLite --
and SQLite is not what production runs. It has no ``vector`` extension, so
``document_chunks.embedding`` there is an opaque ``VECTOR(768)`` *column name*
rather than a real pgvector column with a distance operator behind it. SQLite
also ignores foreign keys, rewrites the whole table for an ``ALTER`` instead of
performing one, and renders ``sa.Float()`` as ``FLOAT`` where PostgreSQL stores
``DOUBLE PRECISION``. A migration can be perfectly green on SQLite and still
fail, or silently behave differently, on the database the application actually
uses.

So this module drives the same runner against a throwaway PostgreSQL database
and asserts the properties that only exist there:

* ``alembic upgrade head`` succeeds on a real server and stamps the real head;
* the migration creates the ``vector`` extension and a genuine ``vector(768)``
  column, with no help from the application;
* the migrated schema is the schema the ORM models describe;
* rows written before the upgrade survive it with the documented defaults;
* upgrade -> downgrade -> upgrade round-trips;
* ``app.core.schema.ensure_schema`` -- the startup reconciliation path -- agrees
  with the migration history on PostgreSQL, both when bootstrapping an empty
  database and when refusing one that is behind head.

The existing SQLite coverage is deliberately left in place: it is fast, it runs
on every push without a server, and it is where the baseline-snapshot and
unmanaged-database assertions live. This module is the part that cannot be
faked, and it is the only coverage of the ``vector(768)`` column actually being
a pgvector column.

Run them with the repository's Docker Compose database::

    docker compose up -d postgres
    TEST_DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/ai_learning_assistant \\
        python -m pytest -m postgres

Without ``TEST_DATABASE_URL`` (or when it is not a PostgreSQL URL) every test in
this module skips instantly, which keeps the default SQLite suite unaffected.
"""

import asyncio
import uuid
from argparse import Namespace
from datetime import datetime
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

import app.models  # noqa: F401  (registers every table on Base.metadata)
from app.core.database import Base
from app.core.schema import (
    ALEMBIC_VERSION_TABLE,
    BASELINE_REVISION,
    SchemaOutOfDateError,
    current_revision,
    ensure_schema,
)
from tests.conftest import requires_postgres
from tests.conftest import test_postgres_url as postgres_url

pytestmark = pytest.mark.postgres

# Pinned deliberately, exactly as tests/test_migrations.py pins it: adding a
# migration must update this constant, or the "did upgrade head really apply
# everything" assertions stop proving anything.
HEAD = "0003_token_lifecycle"
BACKEND_ROOT = Path(__file__).resolve().parents[2]

# The migration runner is driven through ``alembic`` the way an operator drives
# it -- its own connections, its own database -- so isolation has to happen at
# the database level. One test's schema must never be the next test's starting
# point.
ADMIN_DATABASE = "postgres"
SCRATCH_DATABASE_PREFIX = "test_migrations_"


# ---------------------------------------------------------------------------
# Connection helpers
#
# These tests are deliberately *synchronous*. ``migrations/env.py`` runs the
# migration under ``asyncio.run``, which cannot be nested inside a running event
# loop, so an ``async def`` test cannot call the Alembic API at all. The async
# helpers below bridge that gap, and the assertions read back through asyncpg
# for the same reason: psycopg2 is deliberately not a dependency, so there is
# no synchronous PostgreSQL driver to inspect through.
# ---------------------------------------------------------------------------


def _database_url(database: str) -> str:
    """``TEST_DATABASE_URL`` with the database name replaced."""
    return make_url(postgres_url()).set(database=database).render_as_string(
        hide_password=False
    )


def _run(coroutine):
    """Runs a coroutine from a synchronous test body."""
    return asyncio.run(coroutine)


async def _admin_execute(statement: str, **params) -> None:
    """Runs one statement against the maintenance database, outside a transaction.

    ``CREATE DATABASE`` and ``DROP DATABASE`` cannot run inside a transaction
    block, hence ``AUTOCOMMIT``.
    """
    engine = create_async_engine(
        _database_url(ADMIN_DATABASE),
        isolation_level="AUTOCOMMIT",
        poolclass=NullPool,
    )
    try:
        async with engine.connect() as connection:
            await connection.execute(text(statement), params)
    finally:
        await engine.dispose()


@pytest.fixture
def pg_migration_database():
    """An empty, throwaway PostgreSQL database with no tables at all."""
    base_url = postgres_url()
    requires_postgres(base_url)

    name = f"{SCRATCH_DATABASE_PREFIX}{uuid.uuid4().hex[:12]}"
    try:
        _run(_admin_execute(f'CREATE DATABASE "{name}"'))
    except Exception as exc:  # pragma: no cover - depends on local environment
        pytest.skip(f"PostgreSQL is not reachable at {base_url}: {exc}")

    try:
        yield _database_url(name)
    finally:
        try:
            # A leaked connection would make the drop fail and leave the
            # database behind for the rest of the run, so the backends are
            # terminated first.
            _run(
                _admin_execute(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity"
                    " WHERE datname = :name AND pid <> pg_backend_pid()",
                    name=name,
                )
            )
            _run(_admin_execute(f'DROP DATABASE IF EXISTS "{name}"'))
        except Exception:  # pragma: no cover - teardown must not mask failures
            pass


@pytest.fixture
def migrated_database(pg_migration_database) -> str:
    """A throwaway database already upgraded to the current head."""
    upgrade(pg_migration_database, "head")
    return pg_migration_database


# ---------------------------------------------------------------------------
# Query helpers
# ---------------------------------------------------------------------------


def _scalar(url: str, statement: str, **params):
    async def _query():
        engine = create_async_engine(url, poolclass=NullPool)
        try:
            async with engine.connect() as connection:
                result = await connection.execute(text(statement), params)
                return result.scalar()
        finally:
            await engine.dispose()

    return _run(_query())


def _reflect(url: str, read):
    """Runs one reflection read against ``url`` and returns its plain result.

    The whole inspection has to happen inside ``run_sync``: an ``Inspector``
    that escapes the callback would issue its queries from a plain sync context
    with no greenlet to drive the asyncpg cursor, which raises
    ``MissingGreenlet``.
    """

    async def _inspect():
        engine = create_async_engine(url, poolclass=NullPool)
        try:
            async with engine.connect() as connection:
                return await connection.run_sync(
                    lambda sync: read(sa.inspect(sync))
                )
        finally:
            await engine.dispose()

    return _run(_inspect())


def _table_names(url: str) -> set:
    return _reflect(url, lambda inspector: set(inspector.get_table_names()))


def _columns(url: str, table: str) -> dict:
    return _reflect(
        url,
        lambda inspector: {c["name"]: c for c in inspector.get_columns(table)},
    )


def _index_names(url: str, table: str) -> set:
    return _reflect(url, lambda inspector: {i["name"] for i in inspector.get_indexes(table)})


def _model_index_names(model_table) -> set:
    """Index names the models declare, counting unique constraints as indexes.

    PostgreSQL backs every ``UNIQUE`` constraint with a unique index and reports
    it in ``get_indexes``, so a model that expresses the same rule as a named
    ``UniqueConstraint`` would otherwise look like it had an extra index.
    """
    return {
        index.name for index in model_table.indexes
    } | {
        constraint.name
        for constraint in model_table.constraints
        if constraint.__class__.__name__ == "UniqueConstraint"
        and constraint.name
    }


def _unique_constraints(url: str, table: str) -> set:
    return _reflect(
        url,
        lambda inspector: {
            tuple(u["column_names"]) for u in inspector.get_unique_constraints(table)
        },
    )


def _column_type(url: str, table: str, column: str) -> str:
    """PostgreSQL's own rendering of a column's type, e.g. ``vector(768)``."""
    return _scalar(
        url,
        """
        SELECT format_type(a.atttypid, a.atttypmod)
        FROM pg_attribute a
        JOIN pg_class c ON c.oid = a.attrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE c.relname = :table AND a.attname = :column AND n.nspname = 'public'
        """,
        table=table,
        column=column,
    )


# ---------------------------------------------------------------------------
# Driving the real runner
# ---------------------------------------------------------------------------


def alembic_config(url: str) -> Config:
    cfg = Config(str(BACKEND_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_ROOT / "migrations"))
    cfg.set_main_option("sqlalchemy.url", url)
    # migrations/env.py reads the override from -x; the programmatic API takes
    # the same argument here.
    cfg.cmd_opts = Namespace(x=[f"db_url={url}"])
    return cfg


def alembic_heads(url: str) -> list:
    """Head revision(s) as Alembic itself resolves them from the scripts."""
    return ScriptDirectory.from_config(alembic_config(url)).get_heads()


def upgrade(url: str, target: str = "head") -> None:
    command.upgrade(alembic_config(url), target)


def downgrade(url: str, target: str = "base") -> None:
    command.downgrade(alembic_config(url), target)


def _seed(url: str, statements: list, **params) -> None:
    """Runs raw statements, for writing rows at a known migration level."""

    async def _statements():
        engine = create_async_engine(url, poolclass=NullPool)
        try:
            async with engine.begin() as connection:
                for statement in statements:
                    await connection.execute(text(statement), params)
        finally:
            await engine.dispose()

    _run(_statements())


def _stamped_revision(url: str) -> str:
    async def _read():
        engine = create_async_engine(url, poolclass=NullPool)
        try:
            async with engine.connect() as connection:
                return await connection.run_sync(current_revision)
        finally:
            await engine.dispose()

    return _run(_read())


def _ensure_schema(url: str) -> None:
    """Runs the application startup reconciliation against ``url``."""

    async def _run_ensure():
        engine = create_async_engine(url, poolclass=NullPool)
        try:
            async with engine.begin() as connection:
                await connection.run_sync(ensure_schema)
        finally:
            await engine.dispose()

    _run(_run_ensure())


# ---------------------------------------------------------------------------
# The migrations really run on PostgreSQL
# ---------------------------------------------------------------------------


def test_upgrade_head_succeeds_on_postgresql_and_stamps_the_real_head(
    migrated_database,
):
    """``alembic upgrade head`` against a live server, then confirm what it wrote.

    The revision is read back out of ``alembic_version`` rather than trusted,
    and compared against Alembic's own resolution of head, so a migration that
    silently no-ops, or a scripts directory with a forked head, fails here.
    """
    assert alembic_heads(migrated_database) == [HEAD]
    assert _stamped_revision(migrated_database) == HEAD


def test_upgrade_head_installs_pgvector_and_creates_a_real_vector_column(
    migrated_database,
):
    """The migration, not the application, creates the vector extension.

    On SQLite the embedding column is just a column named ``VECTOR(768)``; the
    ``<=>`` operator and the type's distance semantics only exist on a server
    that really has pgvector. Asserting the registered extension and the
    column's formatted type is what makes this a PostgreSQL test rather than an
    SQLite one behind a different driver.

    ``pgvector/pgvector`` ships the extension files but not an enabled
    extension, and a managed PostgreSQL needs it turned on explicitly, which is
    what migration 0001 does for an ordinary deployment.
    """
    assert (
        _scalar(
            migrated_database,
            "SELECT extname FROM pg_extension WHERE extname = 'vector'",
        )
        == "vector"
    )
    assert (
        _column_type(migrated_database, "document_chunks", "embedding")
        == "vector(768)"
    )

    # The column has to behave like a vector, not just be named like one: the
    # cosine-distance operator the retriever relies on only compiles for the
    # real type.
    assert (
        _scalar(
            migrated_database,
            "SELECT '[1,0]'::vector <=> '[0,1]'::vector",
        )
        == pytest.approx(1.0)
    )


def test_migrated_postgresql_schema_matches_the_models(migrated_database):
    """A migrated PostgreSQL database is the schema the ORM models describe.

    Compares table names, column names, nullability, indexes and unique
    constraints. Rendered type strings are deliberately not compared for every
    column, because PostgreSQL reflects ``sa.Float()`` as ``DOUBLE PRECISION``
    and a blanket string comparison reports a difference on a schema that is in
    fact identical. The types that would actually be dangerous to get wrong --
    the vector width, the UUID keys, the token_version column added by 0003 --
    are asserted by value instead.
    """
    reflected_tables = _table_names(migrated_database)
    assert reflected_tables - set(Base.metadata.tables) == {ALEMBIC_VERSION_TABLE}
    assert set(Base.metadata.tables) - reflected_tables == set()

    for table in sorted(Base.metadata.tables):
        model_table = Base.metadata.tables[table]
        migrated = _columns(migrated_database, table)
        assert set(migrated) == set(model_table.columns.keys()), f"{table}: columns"

        for name, column in model_table.columns.items():
            assert migrated[name]["nullable"] == column.nullable, f"{table}.{name}"

        assert _index_names(migrated_database, table) == _model_index_names(
            model_table
        ), f"{table}: indexes"
        assert _unique_constraints(migrated_database, table) == {
            tuple(constraint.columns.keys())
            for constraint in model_table.constraints
            if constraint.__class__.__name__ == "UniqueConstraint"
        }, f"{table}: unique constraints"

    assert (
        _column_type(migrated_database, "document_chunks", "embedding")
        == "vector(768)"
    )
    assert _column_type(migrated_database, "documents", "file_size_bytes") == "integer"
    assert _column_type(migrated_database, "users", "token_version") == "integer"
    assert _column_type(migrated_database, "users", "id") == "uuid"
    assert (
        _column_type(migrated_database, "flashcards", "due_at")
        == "timestamp with time zone"
    )


def test_upgrade_head_on_postgresql_preserves_baseline_rows(pg_migration_database):
    """An existing installation's rows survive the newer migrations.

    PostgreSQL is where ``ADD COLUMN ... NOT NULL`` meets a populated table, so
    this is the case SQLite cannot really represent: existing rows must come
    out the other side with the documented defaults rather than failing the
    upgrade.
    """
    upgrade(pg_migration_database, BASELINE_REVISION)

    user_id, subject_id, deck_id, card_id = (uuid.uuid4().hex for _ in range(4))
    # asyncpg binds a timestamptz parameter as a datetime, where SQLite would
    # accept the string.
    now = datetime(2026, 3, 1, 12, 0, 0)
    _seed(
        pg_migration_database,
        [
            "INSERT INTO users (id, email, full_name, hashed_password, created_at)"
            " VALUES (:user_id, 'baseline@example.com', 'A', 'x', :now)",
            "INSERT INTO subjects (id, user_id, title, color, created_at)"
            " VALUES (:subject_id, :user_id, 'Maths', '#fff', :now)",
            "INSERT INTO flashcard_decks"
            " (id, user_id, subject_id, title, card_count, created_at)"
            " VALUES (:deck_id, :user_id, :subject_id, 'Deck', 1, :now)",
            "INSERT INTO flashcards"
            " (id, deck_id, front_text, back_text, position, created_at)"
            " VALUES (:card_id, :deck_id, 'front', 'back', 0, :now)",
        ],
        user_id=user_id,
        subject_id=subject_id,
        deck_id=deck_id,
        card_id=card_id,
        now=now,
    )

    upgrade(pg_migration_database, HEAD)

    card = _scalar(
        pg_migration_database,
        "SELECT front_text || '|' || back_text || '|' || ease_factor || '|' ||"
        " interval_days || '|' || repetitions || '|' || lapses"
        " FROM flashcards WHERE id = :card_id",
        card_id=card_id,
    )
    assert card == "front|back|2.5|0|0|0"
    # A null due date is the "due now" marker, so an existing deck is
    # immediately reviewable rather than invisible until someone studies it.
    assert (
        _scalar(
            pg_migration_database,
            "SELECT due_at FROM flashcards WHERE id = :card_id",
            card_id=card_id,
        )
        is None
    )
    # 0003 added a NOT NULL column, with a server default, to a populated table.
    assert (
        _scalar(
            pg_migration_database,
            "SELECT token_version FROM users WHERE id = :user_id",
            user_id=user_id,
        )
        == 0
    )
    assert _stamped_revision(pg_migration_database) == HEAD


def test_downgrade_and_upgrade_round_trip_on_postgresql(pg_migration_database):
    """upgrade -> downgrade -> upgrade must land on the same schema."""
    upgrade(pg_migration_database, "head")
    before = set(_columns(pg_migration_database, "flashcards"))

    # Down to the baseline first: 0001's own downgrade drops the table, so the
    # "the migration only removed what it added" question has to be asked while
    # the table still exists.
    downgrade(pg_migration_database, BASELINE_REVISION)
    baseline = set(_columns(pg_migration_database, "flashcards"))
    for column in ("due_at", "ease_factor", "interval_days", "repetitions", "lapses"):
        assert column not in baseline
    assert "token_version" not in set(_columns(pg_migration_database, "users"))
    assert "refresh_tokens" not in _table_names(pg_migration_database)

    downgrade(pg_migration_database, "base")
    assert set(Base.metadata.tables).intersection(_table_names(pg_migration_database)) == set()
    # alembic_version itself survives, or Alembic would have nothing recording
    # that it is back at the beginning.
    assert ALEMBIC_VERSION_TABLE in _table_names(pg_migration_database)

    upgrade(pg_migration_database, "head")
    assert set(_columns(pg_migration_database, "flashcards")) == before
    assert _stamped_revision(pg_migration_database) == HEAD


# ---------------------------------------------------------------------------
# The startup reconciliation path agrees, on PostgreSQL
# ---------------------------------------------------------------------------


def test_ensure_schema_bootstraps_an_empty_postgresql_database(pg_migration_database):
    """The fresh-install path creates and stamps a real PostgreSQL schema.

    This is the code the application runs on startup, so it has to work on the
    database production uses -- including creating the vector extension, since
    a managed PostgreSQL does not ship with pgvector enabled the way the
    container image does.
    """
    _ensure_schema(pg_migration_database)

    assert set(Base.metadata.tables).issubset(_table_names(pg_migration_database))
    assert (
        _scalar(
            pg_migration_database,
            f"SELECT version_num FROM {ALEMBIC_VERSION_TABLE}",
        )
        == HEAD
    )
    assert (
        _scalar(
            pg_migration_database,
            "SELECT extname FROM pg_extension WHERE extname = 'vector'",
        )
        == "vector"
    )
    assert (
        _column_type(pg_migration_database, "document_chunks", "embedding")
        == "vector(768)"
    )

    # Running it again must be a no-op rather than an error: every subsequent
    # restart depends on that, and it is what keeps serving a schema it does
    # not understand from being fatal.
    _ensure_schema(pg_migration_database)


def test_ensure_schema_refuses_a_postgresql_database_behind_head(pg_migration_database):
    """A database behind the code must stop the app, on PostgreSQL too.

    Upgrading only as far as the baseline leaves the schema in exactly the
    state the app must refuse, so the guard is exercised against a real server
    rather than a SQLite approximation of one.
    """
    upgrade(pg_migration_database, BASELINE_REVISION)

    with pytest.raises(SchemaOutOfDateError) as excinfo:
        _ensure_schema(pg_migration_database)

    message = str(excinfo.value)
    assert BASELINE_REVISION in message
    assert HEAD in message
    assert "alembic upgrade head" in message
