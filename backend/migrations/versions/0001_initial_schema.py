"""Initial schema: baseline of the AI Learning Assistant as of Phase 8.

Revision ID: 0001_initial_schema
Revises:
Create Date: 2026-09-27

This is the squashed baseline of every table created by ``Base.metadata.create_all``
before Alembic was introduced. It reproduces that schema exactly so an existing
database can be adopted with ``alembic stamp 0001`` instead of being dropped.

Ordering below follows foreign-key dependencies: identity, then subjects, then
the document/RAG tables, then the three study subsystems (quiz, mastery, study
plan, flashcards). Downgrade reverses it.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects.postgresql import UUID

revision: str = "0001_initial_schema"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _uuid_pk() -> sa.Column:
    return sa.Column("id", UUID(as_uuid=True), nullable=False)


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        # The application never creates this itself; the pgvector Docker image
        # ships it but a managed Postgres usually needs it enabled explicitly.
        op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    # ---------------------------------------------------------------- identity
    op.create_table(
        "users",
        _uuid_pk(),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("hashed_password", sa.String(length=255), nullable=False),
        sa.Column("full_name", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_users"),
    )
    op.create_index("ix_users_email", "users", ["email"], unique=True)

    op.create_table(
        "subjects",
        _uuid_pk(),
        sa.Column(
            "user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("color", sa.String(length=50), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_subjects"),
    )
    op.create_index("ix_subjects_user_id", "subjects", ["user_id"])

    # ------------------------------------------------------- documents and RAG
    op.create_table(
        "documents",
        _uuid_pk(),
        sa.Column(
            "subject_id",
            UUID(as_uuid=True),
            sa.ForeignKey("subjects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("file_path", sa.Text(), nullable=False),
        sa.Column("file_size_bytes", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=50), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_documents"),
    )
    op.create_index("ix_documents_subject_id", "documents", ["subject_id"])
    op.create_index("ix_documents_user_id", "documents", ["user_id"])

    op.create_table(
        "document_chunks",
        _uuid_pk(),
        sa.Column(
            "document_id",
            UUID(as_uuid=True),
            sa.ForeignKey("documents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "subject_id",
            UUID(as_uuid=True),
            sa.ForeignKey("subjects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("page_number", sa.Integer(), nullable=False),
        sa.Column("token_count", sa.Integer(), nullable=False),
        # Nullable: a chunk is insertable before the embedding call returns, and
        # the retriever skips NULL vectors rather than failing the query.
        sa.Column("embedding", Vector(768), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_document_chunks"),
    )
    op.create_index("ix_document_chunks_document_id", "document_chunks", ["document_id"])
    op.create_index("ix_document_chunks_subject_id", "document_chunks", ["subject_id"])
    op.create_index("ix_document_chunks_user_id", "document_chunks", ["user_id"])

    # ------------------------------------------------------------------ quizzes
    op.create_table(
        "quizzes",
        _uuid_pk(),
        sa.Column(
            "user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "subject_id",
            UUID(as_uuid=True),
            sa.ForeignKey("subjects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("difficulty", sa.String(length=20), nullable=False),
        sa.Column("question_count", sa.Integer(), nullable=False),
        sa.Column("topic", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_quizzes"),
    )
    op.create_index("ix_quizzes_user_id", "quizzes", ["user_id"])
    op.create_index("ix_quizzes_subject_id", "quizzes", ["subject_id"])

    op.create_table(
        "quiz_questions",
        _uuid_pk(),
        sa.Column(
            "quiz_id",
            UUID(as_uuid=True),
            sa.ForeignKey("quizzes.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("question_text", sa.Text(), nullable=False),
        sa.Column("option_a", sa.Text(), nullable=False),
        sa.Column("option_b", sa.Text(), nullable=False),
        sa.Column("option_c", sa.Text(), nullable=False),
        sa.Column("option_d", sa.Text(), nullable=False),
        sa.Column("correct_option", sa.String(length=1), nullable=False),
        sa.Column("explanation", sa.Text(), nullable=False),
        sa.Column("difficulty", sa.String(length=20), nullable=False),
        sa.Column("topic", sa.Text(), nullable=True),
        # Citations are SET NULL on purpose: the snapshot columns below keep a
        # question's provenance readable after its source document is deleted.
        sa.Column(
            "document_id",
            UUID(as_uuid=True),
            sa.ForeignKey("documents.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "chunk_id",
            UUID(as_uuid=True),
            sa.ForeignKey("document_chunks.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("document_title", sa.String(length=255), nullable=True),
        sa.Column("page_number", sa.Integer(), nullable=True),
        sa.Column("source_snippet", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_quiz_questions"),
    )
    op.create_index("ix_quiz_questions_quiz_id", "quiz_questions", ["quiz_id"])

    op.create_table(
        "quiz_attempts",
        _uuid_pk(),
        sa.Column(
            "user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "quiz_id",
            UUID(as_uuid=True),
            sa.ForeignKey("quizzes.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "subject_id",
            UUID(as_uuid=True),
            sa.ForeignKey("subjects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("score", sa.Integer(), nullable=False),
        sa.Column("total_questions", sa.Integer(), nullable=False),
        sa.Column("percentage", sa.Float(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_quiz_attempts"),
    )
    op.create_index("ix_quiz_attempts_user_id", "quiz_attempts", ["user_id"])
    op.create_index("ix_quiz_attempts_quiz_id", "quiz_attempts", ["quiz_id"])
    op.create_index("ix_quiz_attempts_subject_id", "quiz_attempts", ["subject_id"])

    op.create_table(
        "quiz_attempt_answers",
        _uuid_pk(),
        sa.Column(
            "attempt_id",
            UUID(as_uuid=True),
            sa.ForeignKey("quiz_attempts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "question_id",
            UUID(as_uuid=True),
            sa.ForeignKey("quiz_questions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("question_text", sa.Text(), nullable=False),
        sa.Column("selected_answer", sa.String(length=8), nullable=False),
        sa.Column("correct_answer", sa.String(length=8), nullable=False),
        sa.Column("is_correct", sa.Boolean(), nullable=False),
        sa.Column("difficulty", sa.String(length=20), nullable=False),
        sa.Column("topic", sa.Text(), nullable=True),
        sa.Column("time_taken", sa.Float(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_quiz_attempt_answers"),
    )
    op.create_index(
        "ix_quiz_attempt_answers_attempt_id", "quiz_attempt_answers", ["attempt_id"]
    )
    op.create_index(
        "ix_quiz_attempt_answers_question_id", "quiz_attempt_answers", ["question_id"]
    )

    # ------------------------------------------------------------------ mastery
    op.create_table(
        "topic_mastery",
        _uuid_pk(),
        sa.Column(
            "user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "subject_id",
            UUID(as_uuid=True),
            sa.ForeignKey("subjects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("topic", sa.Text(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("questions_attempted", sa.Integer(), nullable=False),
        sa.Column("questions_correct", sa.Integer(), nullable=False),
        sa.Column("questions_incorrect", sa.Integer(), nullable=False),
        sa.Column("accuracy", sa.Float(), nullable=False),
        sa.Column("mastery_score", sa.Float(), nullable=False),
        sa.Column("mastery_level", sa.String(length=20), nullable=False),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_topic_mastery"),
        sa.UniqueConstraint(
            "user_id", "subject_id", "topic", name="uq_topic_mastery_user_subject_topic"
        ),
    )
    op.create_index("ix_topic_mastery_user_id", "topic_mastery", ["user_id"])
    op.create_index("ix_topic_mastery_subject_id", "topic_mastery", ["subject_id"])

    # ------------------------------------------------------------- study plans
    op.create_table(
        "study_plans",
        _uuid_pk(),
        sa.Column(
            "user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "subject_id",
            UUID(as_uuid=True),
            sa.ForeignKey("subjects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("subject_title", sa.String(length=255), nullable=False),
        sa.Column("target_date", sa.Date(), nullable=True),
        sa.Column("material_available", sa.Boolean(), nullable=False),
        sa.Column("omitted_topic_count", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_study_plans"),
    )
    op.create_index("ix_study_plans_user_id", "study_plans", ["user_id"])
    op.create_index("ix_study_plans_subject_id", "study_plans", ["subject_id"])

    op.create_table(
        "study_plan_sessions",
        _uuid_pk(),
        sa.Column(
            "plan_id",
            UUID(as_uuid=True),
            sa.ForeignKey("study_plans.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("session_number", sa.Integer(), nullable=False),
        sa.Column("scheduled_date", sa.Date(), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_study_plan_sessions"),
        sa.UniqueConstraint(
            "plan_id", "session_number", name="uq_study_plan_session_number"
        ),
    )
    op.create_index(
        "ix_study_plan_sessions_plan_id", "study_plan_sessions", ["plan_id"]
    )

    op.create_table(
        "study_plan_items",
        _uuid_pk(),
        sa.Column(
            "session_id",
            UUID(as_uuid=True),
            sa.ForeignKey("study_plan_sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("topic", sa.Text(), nullable=False),
        sa.Column("action", sa.String(length=20), nullable=False),
        sa.Column("priority", sa.Float(), nullable=False),
        sa.Column("estimated_minutes", sa.Integer(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("completed", sa.Boolean(), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("material_available", sa.Boolean(), nullable=False),
        sa.Column("mastery", sa.Float(), nullable=False),
        sa.Column("accuracy", sa.Float(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("questions_attempted", sa.Integer(), nullable=False),
        sa.Column("questions_incorrect", sa.Integer(), nullable=False),
        sa.Column("suggested_difficulty", sa.String(length=20), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_study_plan_items"),
    )
    op.create_index("ix_study_plan_items_session_id", "study_plan_items", ["session_id"])

    # --------------------------------------------------------------- flashcards
    op.create_table(
        "flashcard_decks",
        _uuid_pk(),
        sa.Column(
            "user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "subject_id",
            UUID(as_uuid=True),
            sa.ForeignKey("subjects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("topic", sa.Text(), nullable=True),
        sa.Column("card_count", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_flashcard_decks"),
    )
    op.create_index("ix_flashcard_decks_user_id", "flashcard_decks", ["user_id"])
    op.create_index("ix_flashcard_decks_subject_id", "flashcard_decks", ["subject_id"])

    op.create_table(
        "flashcards",
        _uuid_pk(),
        sa.Column(
            "deck_id",
            UUID(as_uuid=True),
            sa.ForeignKey("flashcard_decks.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("front_text", sa.Text(), nullable=False),
        sa.Column("back_text", sa.Text(), nullable=False),
        sa.Column("hint", sa.Text(), nullable=True),
        sa.Column("topic", sa.Text(), nullable=True),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column(
            "document_id",
            UUID(as_uuid=True),
            sa.ForeignKey("documents.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "chunk_id",
            UUID(as_uuid=True),
            sa.ForeignKey("document_chunks.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("document_title", sa.String(length=255), nullable=True),
        sa.Column("page_number", sa.Integer(), nullable=True),
        sa.Column("source_snippet", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_flashcards"),
    )
    op.create_index("ix_flashcards_deck_id", "flashcards", ["deck_id"])

    op.create_table(
        "flashcard_reviews",
        _uuid_pk(),
        sa.Column(
            "user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "card_id",
            UUID(as_uuid=True),
            sa.ForeignKey("flashcards.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "deck_id",
            UUID(as_uuid=True),
            sa.ForeignKey("flashcard_decks.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "subject_id",
            UUID(as_uuid=True),
            sa.ForeignKey("subjects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("is_correct", sa.Boolean(), nullable=False),
        sa.Column("response", sa.Text(), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_flashcard_reviews"),
    )
    op.create_index("ix_flashcard_reviews_user_id", "flashcard_reviews", ["user_id"])
    op.create_index("ix_flashcard_reviews_card_id", "flashcard_reviews", ["card_id"])
    op.create_index("ix_flashcard_reviews_deck_id", "flashcard_reviews", ["deck_id"])
    op.create_index("ix_flashcard_reviews_subject_id", "flashcard_reviews", ["subject_id"])


def downgrade() -> None:
    op.drop_table("flashcard_reviews")
    op.drop_table("flashcards")
    op.drop_table("flashcard_decks")
    op.drop_table("study_plan_items")
    op.drop_table("study_plan_sessions")
    op.drop_table("study_plans")
    op.drop_table("topic_mastery")
    op.drop_table("quiz_attempt_answers")
    op.drop_table("quiz_attempts")
    op.drop_table("quiz_questions")
    op.drop_table("quizzes")
    op.drop_table("document_chunks")
    op.drop_table("documents")
    op.drop_table("subjects")
    op.drop_table("users")
