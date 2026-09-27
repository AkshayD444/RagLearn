"""Schema and migration coverage.

The failure this module exists to prevent: a developer adds a column, restarts
the server, and the app appears healthy because ``create_all`` silently ignores
``ALTER``. The first query that touches the column then fails at runtime with
``UndefinedColumn`` on a database that ``alembic_version`` claims is current.

Two properties are asserted here, both against a real SQLite database built by
the real Alembic runner rather than by calling ``upgrade()`` directly:

1. ``alembic upgrade head`` produces exactly the schema the models describe, so
   a migrated database and a bootstrapped one are indistinguishable.
2. A pre-Phase-9 row survives the upgrade, and the downgrade removes only what
   the migration added.
"""

import os
import uuid
from argparse import Namespace

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config

import app.models  # noqa: F401  (registers every table on Base.metadata)
from app.core.database import Base
from app.core.schema import (
    ALEMBIC_VERSION_TABLE,
    BASELINE_REVISION,
    SchemaOutOfDateError,
    _head_revision,
    current_revision,
    ensure_schema,
)

HEAD = "0002_flashcard_scheduling"
BACKEND_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _alembic_config(db_path: str) -> Config:
    cfg = Config(os.path.join(BACKEND_ROOT, "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(BACKEND_ROOT, "migrations"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite+aiosqlite:///{db_path}")
    # migrations/env.py reads the override from -x; alembic's programmatic API
    # takes the same argument here.
    cfg.cmd_opts = Namespace(x=[f"db_url=sqlite+aiosqlite:///{db_path}"])
    return cfg


@pytest.fixture
def db_path(tmp_path) -> str:
    return str(tmp_path / "migration_test.db")


@pytest.fixture
def engine(db_path):
    # A file-backed engine, because the Alembic runner opens its own connection.
    eng = sa.create_engine(f"sqlite:///{db_path}")
    yield eng
    eng.dispose()


def _upgrade(db_path: str, target: str) -> None:
    command.upgrade(_alembic_config(db_path), target)


def _downgrade(db_path: str, target: str) -> None:
    command.downgrade(_alembic_config(db_path), target)


def _columns(inspector, table: str) -> set:
    return {c["name"] for c in inspector.get_columns(table)}


# ---------------------------------------------------------------------------
# Head resolution
# ---------------------------------------------------------------------------


def test_head_revision_matches_the_migration_history():
    """``_head_revision`` delegates to Alembic, so it cannot drift from reality."""
    assert _head_revision() == HEAD


# ---------------------------------------------------------------------------
# Parity between the migrations and the models
# ---------------------------------------------------------------------------


def test_upgrade_head_matches_the_models(engine, db_path):
    _upgrade(db_path, "head")

    # Build the model schema the way the bootstrap path does, so the two are
    # compared through reflection on the same dialect rather than by reading DDL.
    bootstrapped_engine = sa.create_engine("sqlite://")
    Base.metadata.create_all(bind=bootstrapped_engine)
    migrated = sa.inspect(engine)
    bootstrapped = sa.inspect(bootstrapped_engine)

    assert set(migrated.get_table_names()) - set(bootstrapped.get_table_names()) == {
        ALEMBIC_VERSION_TABLE
    }
    assert set(bootstrapped.get_table_names()) - set(migrated.get_table_names()) == set()

    for table in sorted(bootstrapped.get_table_names()):
        m_cols = {c["name"]: c for c in migrated.get_columns(table)}
        b_cols = {c["name"]: c for c in bootstrapped.get_columns(table)}
        assert set(b_cols) == set(m_cols), f"{table}: column set differs"
        for name, col in b_cols.items():
            assert str(m_cols[name]["type"]) == str(col["type"]), f"{table}.{name}: type"
            assert m_cols[name]["nullable"] == col["nullable"], f"{table}.{name}: nullable"

        m_idx = {i["name"] for i in migrated.get_indexes(table)}
        b_idx = {i["name"] for i in bootstrapped.get_indexes(table)}
        assert m_idx == b_idx, f"{table}: index set differs ({b_idx - m_idx})"

        m_uq = {tuple(u["column_names"]) for u in migrated.get_unique_constraints(table)}
        b_uq = {tuple(u["column_names"]) for u in bootstrapped.get_unique_constraints(table)}
        assert m_uq == b_uq, f"{table}: unique constraints differ"


def test_flashcard_schedule_columns_are_indexed(engine, db_path):
    """The due queue is a range scan; an unindexed ``due_at`` would be a table scan."""
    _upgrade(db_path, "head")
    indexes = {i["name"] for i in sa.inspect(engine).get_indexes("flashcards")}
    assert "ix_flashcards_due_at" in indexes
    assert "ix_flashcards_deck_due_at" in indexes


def test_baseline_has_no_scheduling_columns(engine, db_path):
    """0001 must stay a faithful snapshot of Phase 8, not a stealth Phase 9."""
    _upgrade(db_path, BASELINE_REVISION)
    columns = _columns(sa.inspect(engine), "flashcards")
    for column in ("due_at", "ease_factor", "interval_days", "repetitions", "lapses"):
        assert column not in columns


# ---------------------------------------------------------------------------
# Data survives the upgrade
# ---------------------------------------------------------------------------


def test_upgrade_preserves_a_phase_8_card(engine, db_path):
    """An existing installation's cards must become due, not vanish or error."""
    _upgrade(db_path, BASELINE_REVISION)

    now = "2026-03-01 12:00:00.000000"
    with engine.begin() as conn:
        uid, sid, did, cid = (uuid.uuid4().hex for _ in range(4))
        conn.execute(
            sa.text(
                "INSERT INTO users (id, email, full_name, hashed_password, created_at)"
                " VALUES (:i, 'a@b.co', 'A', 'x', :t)"
            ),
            {"i": uid, "t": now},
        )
        conn.execute(
            sa.text(
                "INSERT INTO subjects (id, user_id, title, color, created_at)"
                " VALUES (:i, :u, 'Maths', '#fff', :t)"
            ),
            {"i": sid, "u": uid, "t": now},
        )
        conn.execute(
            sa.text(
                "INSERT INTO flashcard_decks"
                " (id, user_id, subject_id, title, card_count, created_at)"
                " VALUES (:i, :u, :s, 'Deck', 1, :t)"
            ),
            {"i": did, "u": uid, "s": sid, "t": now},
        )
        conn.execute(
            sa.text(
                "INSERT INTO flashcards (id, deck_id, front_text, back_text, position, created_at)"
                " VALUES (:i, :d, 'front', 'back', 0, :t)"
            ),
            {"i": cid, "d": did, "t": now},
        )

    _upgrade(db_path, HEAD)

    with engine.connect() as conn:
        row = conn.execute(
            sa.text(
                "SELECT front_text, back_text, ease_factor, interval_days,"
                " repetitions, lapses, due_at FROM flashcards"
            )
        ).one()

    assert row[0] == "front" and row[1] == "back"
    assert (row[2], row[3], row[4], row[5]) == (2.5, 0, 0, 0)
    # A null due date is the "due now" marker, so an existing deck is immediately
    # reviewable rather than invisible until someone studies it.
    assert row[6] is None


def test_downgrade_removes_only_what_the_migration_added(engine, db_path):
    _upgrade(db_path, HEAD)
    _downgrade(db_path, BASELINE_REVISION)

    inspector = sa.inspect(engine)
    flashcards = _columns(inspector, "flashcards")
    for column in ("due_at", "ease_factor", "interval_days", "repetitions", "lapses"):
        assert column not in flashcards
    assert not any(c.startswith("flashcard_") for c in _columns(inspector, "topic_mastery"))
    assert "due_cards" not in _columns(inspector, "study_plan_items")


def test_migration_is_idempotent_across_a_round_trip(engine, db_path):
    """upgrade -> downgrade -> upgrade must land on the same schema."""
    _upgrade(db_path, HEAD)
    first = _columns(sa.inspect(engine), "flashcards")
    _downgrade(db_path, BASELINE_REVISION)
    _upgrade(db_path, HEAD)
    assert _columns(sa.inspect(engine), "flashcards") == first


# ---------------------------------------------------------------------------
# Startup reconciliation
# ---------------------------------------------------------------------------


def test_ensure_schema_bootstraps_an_empty_database(engine):
    with engine.begin() as conn:
        ensure_schema(conn)

    inspector = sa.inspect(engine)
    assert set(Base.metadata.tables).issubset(set(inspector.get_table_names()))
    with engine.connect() as conn:
        assert conn.execute(sa.text(f"SELECT version_num FROM {ALEMBIC_VERSION_TABLE}")).scalar_one() == HEAD


def test_ensure_schema_is_a_no_op_when_current(engine, db_path):
    _upgrade(db_path, HEAD)
    with engine.begin() as conn:
        assert current_revision(conn) == HEAD
        ensure_schema(conn)  # must not raise
    assert current_revision(engine.connect()) == HEAD


def test_ensure_schema_refuses_an_unmanaged_database(engine, db_path):
    """Tables with no ``alembic_version`` must stop the app, not be ignored.

    The error text matters as much as the raise: it has to tell the developer to
    stamp the *baseline*, because stamping head would make Alembic skip 0002 and
    leave the new columns missing while reporting the schema as current.
    """
    _upgrade(db_path, BASELINE_REVISION)
    with engine.begin() as conn:
        conn.execute(sa.text(f"DROP TABLE {ALEMBIC_VERSION_TABLE}"))

    with pytest.raises(SchemaOutOfDateError) as excinfo:
        with engine.begin() as conn:
            ensure_schema(conn)

    message = str(excinfo.value)
    assert BASELINE_REVISION in message
    assert "alembic upgrade head" in message
    # Must not tell anyone to stamp head: that is the trap.
    assert "alembic stamp 0002" not in message


def test_ensure_schema_refuses_a_database_behind_head(engine, db_path):
    _upgrade(db_path, BASELINE_REVISION)
    with pytest.raises(SchemaOutOfDateError) as excinfo:
        with engine.begin() as conn:
            ensure_schema(conn)

    message = str(excinfo.value)
    assert BASELINE_REVISION in message
    assert HEAD in message
    assert "alembic upgrade head" in message
