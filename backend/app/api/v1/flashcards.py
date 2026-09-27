import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import Integer, and_, cast, distinct, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db, require_owned_subject
from app.models.flashcard import Flashcard, FlashcardDeck, FlashcardReview
from app.models.mastery import TopicMastery
from app.models.user import User
from app.schemas.flashcard import (
    FlashcardDeckRead,
    FlashcardDeckSummary,
    FlashcardGenerateRequest,
    FlashcardRead,
    FlashcardReviewItem,
    FlashcardReviewOutcome,
    FlashcardReviewRequest,
    FlashcardReviewSummaryResponse,
    FlashcardTopicProgress,
)
from app.services.flashcard_engine import flashcard_generation_service
from app.services.quiz_engine import NoStudyMaterialsError
from app.services.student_intelligence import normalize_topic

router = APIRouter(prefix="/flashcards", tags=["Flashcards"])

# (reviewed card count, correct count)
CardTotals = Dict[uuid.UUID, Tuple[int, int]]
DeckTotals = Dict[uuid.UUID, Tuple[int, int]]


def _correct_sum() -> Any:
    """A portable ``sum(is_correct)``.

    ``cast`` is required because PostgreSQL has no boolean sum, while SQLite
    accepts one; casting keeps a single expression valid on both dialects.
    """
    return func.coalesce(func.sum(cast(FlashcardReview.is_correct, Integer)), 0)


async def _load_cards(db: AsyncSession, deck_id: uuid.UUID) -> List[Flashcard]:
    result = await db.execute(
        select(Flashcard)
        .where(Flashcard.deck_id == deck_id)
        .order_by(Flashcard.position, Flashcard.created_at, Flashcard.id)
    )
    return list(result.scalars().all())


async def _load_owned_deck(
    db: AsyncSession, deck_id: uuid.UUID, current_user: User
) -> FlashcardDeck:
    result = await db.execute(
        select(FlashcardDeck).where(
            FlashcardDeck.id == deck_id,
            FlashcardDeck.user_id == current_user.id,
            FlashcardDeck.deleted_at.is_(None),
        )
    )
    deck = result.scalar_one_or_none()
    if not deck:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Flashcard deck not found or access denied.",
        )
    return deck


async def _active_deck_ids(
    db: AsyncSession, *, current_user: User, subject_id: Optional[uuid.UUID]
) -> List[uuid.UUID]:
    """Ids of the user's non-deleted decks, optionally narrowed to one subject."""
    stmt = select(FlashcardDeck.id).where(
        FlashcardDeck.user_id == current_user.id,
        FlashcardDeck.deleted_at.is_(None),
    )
    if subject_id is not None:
        stmt = stmt.where(FlashcardDeck.subject_id == subject_id)
    result = await db.execute(stmt.order_by(FlashcardDeck.created_at.desc()))
    return list(result.scalars().all())


async def _card_review_totals(
    db: AsyncSession, *, current_user: User, card_ids: List[uuid.UUID]
) -> CardTotals:
    """Per-card ``(review_count, correct_count)`` derived from the review log."""
    if not card_ids:
        return {}
    result = await db.execute(
        select(
            FlashcardReview.card_id,
            func.count(FlashcardReview.id),
            _correct_sum(),
        )
        .where(
            FlashcardReview.user_id == current_user.id,
            FlashcardReview.card_id.in_(card_ids),
        )
        .group_by(FlashcardReview.card_id)
    )
    return {
        row[0]: (int(row[1]), int(row[2]))
        for row in result.all()
    }


async def _deck_review_totals(
    db: AsyncSession, *, current_user: User, deck_ids: List[uuid.UUID]
) -> DeckTotals:
    """Per-deck ``(reviewed_card_count, correct_count)`` derived from the log."""
    if not deck_ids:
        return {}
    result = await db.execute(
        select(
            FlashcardReview.deck_id,
            func.count(distinct(FlashcardReview.card_id)),
            _correct_sum(),
        )
        .where(
            FlashcardReview.user_id == current_user.id,
            FlashcardReview.deck_id.in_(deck_ids),
        )
        .group_by(FlashcardReview.deck_id)
    )
    return {
        row[0]: (int(row[1]), int(row[2]))
        for row in result.all()
    }


def _to_card_read(card: Flashcard, totals: Optional[Tuple[int, int]]) -> FlashcardRead:
    review_count, correct_count = totals or (0, 0)
    return FlashcardRead(
        id=card.id,
        front_text=card.front_text,
        back_text=card.back_text,
        hint=card.hint,
        topic=card.topic,
        position=card.position,
        source_document_id=card.document_id,
        source_document_title=card.document_title,
        source_chunk_id=card.chunk_id,
        source_page_number=card.page_number,
        source_snippet=card.source_snippet,
        review_count=review_count,
        correct_count=correct_count,
    )


def _to_deck_read(
    deck: FlashcardDeck, cards: List[Flashcard], totals: CardTotals
) -> FlashcardDeckRead:
    return FlashcardDeckRead(
        id=deck.id,
        subject_id=deck.subject_id,
        title=deck.title,
        topic=deck.topic,
        card_count=deck.card_count,
        created_at=deck.created_at,
        cards=[_to_card_read(card, totals.get(card.id)) for card in cards],
    )


@router.post("/generate", response_model=FlashcardDeckRead, status_code=status.HTTP_201_CREATED)
async def generate_flashcards(
    payload: FlashcardGenerateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Any:
    """Generates a grounded flashcard deck from the user's own subject documents."""
    subject = await require_owned_subject(db, payload.subject_id, current_user)

    try:
        deck = await flashcard_generation_service.generate_deck(
            db,
            subject=subject,
            card_count=payload.card_count,
            topic=payload.topic,
        )
    except NoStudyMaterialsError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "No indexed study materials found for this subject yet. "
                "Upload and process a PDF before generating flashcards."
            ),
        )

    cards = await _load_cards(db, deck.id)
    totals = await _card_review_totals(
        db, current_user=current_user, card_ids=[card.id for card in cards]
    )
    return _to_deck_read(deck, cards, totals)


@router.get("/decks", response_model=List[FlashcardDeckSummary])
async def list_flashcard_decks(
    subject_id: Optional[uuid.UUID] = Query(default=None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Any:
    """Lists the caller's flashcard decks, newest first.

    When ``subject_id`` is supplied the subject must be owned and not deleted,
    so a foreign subject id is indistinguishable from a missing one.
    """
    if subject_id is not None:
        await require_owned_subject(db, subject_id, current_user)

    deck_ids = await _active_deck_ids(
        db, current_user=current_user, subject_id=subject_id
    )
    if not deck_ids:
        return []

    result = await db.execute(
        select(FlashcardDeck)
        .where(FlashcardDeck.id.in_(deck_ids))
        .order_by(FlashcardDeck.created_at.desc(), FlashcardDeck.id.desc())
    )
    decks = list(result.scalars().all())
    totals = await _deck_review_totals(
        db, current_user=current_user, deck_ids=[deck.id for deck in decks]
    )

    summaries = []
    for deck in decks:
        reviewed_card_count, correct_count = totals.get(deck.id, (0, 0))
        summaries.append(
            FlashcardDeckSummary(
                id=deck.id,
                subject_id=deck.subject_id,
                title=deck.title,
                topic=deck.topic,
                card_count=deck.card_count,
                created_at=deck.created_at,
                reviewed_card_count=reviewed_card_count,
                correct_count=correct_count,
            )
        )
    return summaries


@router.get("/decks/{deck_id}", response_model=FlashcardDeckRead)
async def get_flashcard_deck(
    deck_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Any:
    """Returns a single owned deck with its cards and derived review progress."""
    deck = await _load_owned_deck(db, deck_id, current_user)
    cards = await _load_cards(db, deck.id)
    totals = await _card_review_totals(
        db, current_user=current_user, card_ids=[card.id for card in cards]
    )
    return _to_deck_read(deck, cards, totals)


@router.delete("/decks/{deck_id}", status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
async def delete_flashcard_deck(
    deck_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Soft-deletes an owned deck.

    The review log is preserved so per-topic progress stays readable, but the
    deck is no longer listed, retrievable, or reviewable.
    """
    deck = await _load_owned_deck(db, deck_id, current_user)
    deck.deleted_at = datetime.utcnow()
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _validate_reviews(
    reviews: List[FlashcardReviewItem], cards: List[Flashcard]
) -> None:
    expected_ids = {card.id for card in cards}
    provided_ids = [review.card_id for review in reviews]

    if len(provided_ids) != len(set(provided_ids)):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Duplicate reviews for the same card are not allowed.",
        )
    if set(provided_ids) != expected_ids:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Review must include exactly one result for every card in the deck.",
        )


@router.post("/decks/{deck_id}/review", response_model=FlashcardReviewSummaryResponse)
async def review_flashcard_deck(
    deck_id: uuid.UUID,
    payload: FlashcardReviewRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Any:
    """Records a self-graded review of every card in an owned deck.

    Reviews are append-only, so re-reviewing a deck adds history rather than
    replacing it. ``topic_mastery`` is deliberately not touched here: quiz
    evidence remains the only input to the Phase 6/7 mastery and recommendation
    models.
    """
    deck = await _load_owned_deck(db, deck_id, current_user)
    cards = await _load_cards(db, deck.id)
    if not cards:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This flashcard deck has no cards to review.",
        )

    _validate_reviews(payload.reviews, cards)

    submitted = {review.card_id: review for review in payload.reviews}
    reviewed_at = datetime.utcnow()

    for card in cards:
        review = submitted[card.id]
        db.add(
            FlashcardReview(
                user_id=current_user.id,
                card_id=card.id,
                deck_id=deck.id,
                subject_id=deck.subject_id,
                is_correct=review.is_correct,
                response=review.response,
                reviewed_at=reviewed_at,
            )
        )

    await db.commit()

    totals = await _card_review_totals(
        db, current_user=current_user, card_ids=[card.id for card in cards]
    )

    results = []
    correct_count = 0
    for card in cards:
        review = submitted[card.id]
        review_count, card_correct_count = totals.get(card.id, (0, 0))
        if review.is_correct:
            correct_count += 1
        results.append(
            FlashcardReviewOutcome(
                card_id=card.id,
                is_correct=review.is_correct,
                response=review.response,
                review_count=review_count,
                correct_count=card_correct_count,
            )
        )

    reviewed_count = len(results)
    percentage = (
        round(correct_count / reviewed_count * 100, 1) if reviewed_count else 0.0
    )
    return FlashcardReviewSummaryResponse(
        deck_id=deck.id,
        title=deck.title,
        reviewed_count=reviewed_count,
        correct_count=correct_count,
        incorrect_count=reviewed_count - correct_count,
        percentage=percentage,
        results=results,
    )


@router.get(
    "/subjects/{subject_id}/progress", response_model=List[FlashcardTopicProgress]
)
async def get_flashcard_progress(
    subject_id: uuid.UUID,
    limit: int = Query(default=20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Any:
    """Per-topic flashcard progress for a subject, annotated with topic mastery.

    The mastery columns are read from ``topic_mastery`` and never written here.
    """
    await require_owned_subject(db, subject_id, current_user)

    deck_ids = await _active_deck_ids(
        db, current_user=current_user, subject_id=subject_id
    )
    if not deck_ids:
        return []

    result = await db.execute(
        select(
            Flashcard.topic,
            func.count(distinct(Flashcard.id)),
            func.count(FlashcardReview.id),
            _correct_sum(),
        )
        .select_from(Flashcard)
        .outerjoin(
            FlashcardReview,
            and_(
                FlashcardReview.card_id == Flashcard.id,
                FlashcardReview.user_id == current_user.id,
            ),
        )
        .where(Flashcard.deck_id.in_(deck_ids))
        .group_by(Flashcard.topic)
    )

    rows = [
        {
            "topic": normalize_topic(row[0]),
            "card_count": int(row[1]),
            "reviewed_count": int(row[2]),
            "correct_count": int(row[3]),
        }
        for row in result.all()
    ]

    mastery_result = await db.execute(
        select(TopicMastery.topic, TopicMastery.mastery_score, TopicMastery.mastery_level).where(
            TopicMastery.user_id == current_user.id,
            TopicMastery.subject_id == subject_id,
        )
    )
    mastery_by_topic = {
        row[0]: (row[1], row[2]) for row in mastery_result.all()
    }

    progress = []
    for row in rows:
        mastery_score, mastery_level = mastery_by_topic.get(row["topic"], (None, None))
        reviewed_count = row["reviewed_count"]
        progress.append(
            FlashcardTopicProgress(
                subject_id=subject_id,
                topic=row["topic"],
                card_count=row["card_count"],
                reviewed_count=reviewed_count,
                correct_count=row["correct_count"],
                accuracy=(
                    round(row["correct_count"] / reviewed_count * 100, 1)
                    if reviewed_count
                    else 0.0
                ),
                mastery_score=mastery_score,
                mastery_level=mastery_level,
            )
        )

    progress.sort(key=lambda item: (item.reviewed_count, item.topic))
    return progress[:limit]
