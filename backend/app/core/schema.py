"""Database schema bootstrap and version reconciliation.

Why this exists
---------------
The application originally created its schema with an unconditional
``Base.metadata.create_all`` on startup. ``create_all`` only ever *creates*
missing tables: it silently ignores every ``ALTER``. A developer who added a
column, restarted the server, and saw the app keep working would conclude the
column existed, and the first query that touched it would fail at runtime with
``UndefinedColumn``.

Alembic is now the source of truth. The three states this module handles:

* **No database yet** -- create the schema from the models and stamp head, so a
  fresh checkout still runs with no extra steps.
* **Stamped behind head** -- refuse to start. A migration was never applied, so
  columns the code already selects are missing; the only honest options are to
  upgrade or to stop, never to serve and fail later on the first request.
* **Tables but no ``alembic_version``** -- refuse to start and print the exact
  adopt sequence. This is the pre-Alembic database, and it must be stamped at
  the *baseline*, not at head.

That last distinction is the subtle one. ``alembic stamp head`` on a database
that predates migration 0002 tells Alembic the new scheduling columns already
exist. The subsequent ``upgrade head`` is then a no-op, the columns are never
added, and the failure surfaces later as ``UndefinedColumn`` on the due query --
with an ``alembic_version`` table insisting everything is current.

Nothing here drops or alters existing data. The only writes are ``CREATE
TABLE``/``CREATE INDEX`` on a database with no tables at all, and writes to
``alembic_version``.
"""

import pathlib
from typing import List, Optional, Set

from sqlalchemy import inspect, text
from sqlalchemy.engine import Connection

from app.core.database import Base
from app.core.logging_config import get_logger

logger = get_logger("app.schema")

ALEMBIC_VERSION_TABLE = "alembic_version"

BACKEND_ROOT = pathlib.Path(__file__).resolve().parents[2]
ALEMBIC_INI = BACKEND_ROOT / "alembic.ini"

# The first migration, which only creates the pre-Alembic tables. A database
# created by the old `create_all` lifespan matches it exactly, which is why it
# is the correct adopt target for an unmanaged database.
BASELINE_REVISION = "0001_initial_schema"

# Only used if the migration history itself cannot be read; see `_head_revision`.
_FALLBACK_HEAD = "0002_flashcard_scheduling"


class SchemaOutOfDateError(RuntimeError):
    """The live schema does not match what the code expects.

    Fatal on purpose. The alternative -- warn and keep serving -- is the exact
    failure this module was written to eliminate.
    """


def current_revision(connection: Connection) -> Optional[str]:
    """Returns the stamped revision, or ``None`` when the database is unmanaged."""
    inspector = inspect(connection)
    if ALEMBIC_VERSION_TABLE not in inspector.get_table_names():
        return None
    result = connection.execute(
        text(f"SELECT version_num FROM {ALEMBIC_VERSION_TABLE} LIMIT 1")
    )
    return result.scalar()


def _app_tables(connection: Connection) -> Set[str]:
    """Tables the models know about that actually exist in the database."""
    inspector = inspect(connection)
    return set(inspector.get_table_names()).intersection(Base.metadata.tables)


def _head_revision() -> str:
    """The real head, resolved by Alembic's own script directory.

    Delegates to ``ScriptDirectory`` instead of listing ``versions/`` so the
    answer follows ``down_revision`` links: multiple heads and renamed files
    are then reported the way Alembic itself would report them, and a fork
    raises instead of quietly resolving to whichever filename sorts last.
    """
    try:
        from alembic.config import Config
        from alembic.script import ScriptDirectory

        directory = ScriptDirectory.from_config(
            Config(str(ALEMBIC_INI)), script_location=str(BACKEND_ROOT / "migrations")
        )
        heads: List[str] = directory.get_heads()
    except Exception as exc:
        logger.warning(
            "could_not_read_migration_head",
            extra={
                "fallback_revision": _FALLBACK_HEAD,
                "alembic_ini": str(ALEMBIC_INI),
                "error": str(exc),
            },
        )
        return _FALLBACK_HEAD

    if len(heads) > 1:
        raise SchemaOutOfDateError(
            f"Multiple migration heads detected: {sorted(heads)}. "
            "Merge them with `alembic merge` before starting the server."
        )
    return heads[0]


def _ensure_vector_extension(connection: Connection) -> None:
    """Enables pgvector before any Vector column is created.

    Only PostgreSQL has the extension. The test suite runs on SQLite, where the
    pgvector type renders as an opaque ``VECTOR(768)`` column name that SQLite
    accepts without complaint.
    """
    if connection.dialect.name == "postgresql":
        connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))


def _create_and_stamp(connection: Connection, head: str) -> None:
    """Builds a brand-new schema and marks it current.

    Safe to call only when no application table exists: it must never run
    against a populated database, because ``create_all`` would leave that
    database half-migrated and stamped as though it were complete.
    """
    _ensure_vector_extension(connection)
    Base.metadata.create_all(bind=connection)
    connection.execute(
        text(
            f"CREATE TABLE IF NOT EXISTS {ALEMBIC_VERSION_TABLE} "
            "(version_num VARCHAR(32) NOT NULL, "
            "CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num))"
        )
    )
    # `create_all` is not transactional on every backend, so clear any partial
    # stamp rather than leaving two rows that would make `get_heads` ambiguous.
    connection.execute(text(f"DELETE FROM {ALEMBIC_VERSION_TABLE}"))
    connection.execute(
        text(f"INSERT INTO {ALEMBIC_VERSION_TABLE} (version_num) VALUES (:rev)"),
        {"rev": head},
    )
    logger.info(
        "schema_created_and_stamped",
        extra={"revision": head, "tables": len(Base.metadata.tables)},
    )


def ensure_schema(connection: Connection) -> None:
    """Reconciles the live schema with the migration history.

    Called inside ``engine.begin()`` during startup. Any raise aborts the
    transaction and stops the app from serving a schema it does not understand.
    """
    head = _head_revision()
    stamped = current_revision(connection)
    app_tables = _app_tables(connection)

    if stamped == head:
        logger.debug("schema_up_to_date", extra={"revision": head})
        return

    if stamped is None:
        if not app_tables:
            _create_and_stamp(connection, head)
            return
        raise SchemaOutOfDateError(
            f"Found {len(app_tables)} application tables but no "
            f"{ALEMBIC_VERSION_TABLE} table, so this database predates Alembic. "
            f"Adopt it at the {BASELINE_REVISION} baseline and then apply the "
            "newer migrations:\n"
            "    alembic stamp 0001_initial_schema\n"
            "    alembic upgrade head\n"
            f"Stamping {head} directly would be wrong: Alembic would report the "
            "schema as current while the columns added after the baseline are "
            "still missing."
        )

    raise SchemaOutOfDateError(
        f"Database is at revision {stamped} but the code expects {head}. "
        "Apply the pending migrations:\n"
        "    alembic upgrade head\n"
        f"Refusing to start. {len(app_tables)} application tables are present but "
        "the schema is behind the code, so queries that touch the new columns "
        "would fail at runtime."
    )
