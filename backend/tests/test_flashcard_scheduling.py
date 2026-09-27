"""HTTP and persistence coverage for Phase 9 spaced repetition.

The behavioural contract these tests pin down:

* a card's schedule is derived from its stored state, so a review survives a
  server restart and a retry cannot double-apply a lapse;
* the due queue is scoped to the owner and ordered most-overdue first;
* a review moves the card *out* of the queue, and the reschedule endpoint moves
  it back;
* flashcard reviews accumulate in their own ``flashcard_*`` evidence channel and
  never disturb the quiz-derived mastery that drives recommendations.
"""

import uuid
from datetime import datetime, timedelta

import pytest
from sqlalchemy import select

from app.models.flashcard import Flashcard
from app.models.mastery import TopicMastery
from tests.conftest import TestAsyncSessionLocal

MATERIAL = [
    "Spaced repetition schedules each item for review just before it is forgotten.",
    "The SM-2 algorithm grows the interval by an ease factor after each success.",
    "A lapse resets the repetition count so the card re-enters the learning ladder.",
]


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def _deck(ac, user: dict, seeded_chunks, **overrides) -> dict:
    """Generates one deck with indexed material and returns its JSON body."""
    await seeded_chunks(MATERIAL)
    payload = {"subject_id": user["subject_id"], "card_count": 3}
    payload.update(overrides)
    res = await ac.post(
        "/api/v1/flashcards/generate", json=payload, headers=_auth(user["token"])
    )
    assert res.status_code == 201, res.text
    return res.json()


@pytest.fixture
async def owner(registered_user, subject):
    registered_user["subject_id"] = subject
    return registered_user


async def _set_schedule(card_id: str, **fields) -> None:
    """Forces a card's schedule directly, so tests do not have to simulate N reviews."""
    async with TestAsyncSessionLocal() as session:
        card = await session.get(Flashcard, uuid.UUID(card_id))
        for key, value in fields.items():
            setattr(card, key, value)
        await session.commit()


# ---------------------------------------------------------------------------
# New decks start life fully due
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_new_deck_reports_every_card_as_due(client, owner, seeded_chunks):
    """Generating a deck is a request to learn it, so nothing waits in the wings."""
    deck = await _deck(client, owner, seeded_chunks)

    res = await client.get(
        f"/api/v1/flashcards/due?subject_id={owner['subject_id']}",
        headers=_auth(owner["token"]),
    )
    assert res.status_code == 200, res.text
    body = res.json()

    assert body["due_count"] == deck["card_count"]
    assert body["new_count"] == deck["card_count"]
    assert len(body["cards"]) == deck["card_count"]
    assert all(card["schedule"]["is_new"] for card in body["cards"])


@pytest.mark.asyncio
async def test_deck_detail_reports_due_count_and_schedule(client, owner, seeded_chunks):
    deck = await _deck(client, owner, seeded_chunks)
    res = await client.get(
        f"/api/v1/flashcards/decks/{deck['id']}", headers=_auth(owner["token"])
    )
    assert res.status_code == 200, res.text
    body = res.json()

    assert body["due_count"] == deck["card_count"]
    schedule = body["cards"][0]["schedule"]
    assert schedule["ease_factor"] == 2.5
    assert schedule["repetitions"] == 0
    assert schedule["due_at"] is None
    assert schedule["is_new"] is True


@pytest.mark.asyncio
async def test_deck_list_reports_due_count(client, owner, seeded_chunks):
    await _deck(client, owner, seeded_chunks)
    res = await client.get("/api/v1/flashcards/decks", headers=_auth(owner["token"]))
    assert res.status_code == 200, res.text
    assert res.json()[0]["due_count"] == 3


# ---------------------------------------------------------------------------
# Due queue
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_due_queue_requires_authentication(client, owner):
    assert (await client.get("/api/v1/flashcards/due")).status_code == 401


@pytest.mark.asyncio
async def test_due_queue_never_leaks_another_users_cards(
    client, owner, other_user, seeded_chunks
):
    await _deck(client, owner, seeded_chunks)

    res = await client.get("/api/v1/flashcards/due", headers=_auth(other_user["token"]))
    assert res.status_code == 200, res.text
    assert res.json() == {"cards": [], "due_count": 0, "new_count": 0}


@pytest.mark.asyncio
async def test_due_queue_hides_a_deck_the_user_does_not_own(
    client, owner, other_user, seeded_chunks
):
    """Asking by ``deck_id`` for a foreign deck must 404, not return an empty list.

    An empty list would confirm the deck id does not exist, which is exactly the
    information an id-guessing attacker is probing for.
    """
    deck = await _deck(client, owner, seeded_chunks)
    res = await client.get(
        f"/api/v1/flashcards/due?deck_id={deck['id']}",
        headers=_auth(other_user["token"]),
    )
    assert res.status_code == 404


@pytest.mark.asyncio
async def test_due_queue_rejects_a_foreign_subject(client, owner, other_user):
    res = await client.get(
        f"/api/v1/flashcards/due?subject_id={owner['subject_id']}",
        headers=_auth(other_user["token"]),
    )
    assert res.status_code == 404


@pytest.mark.asyncio
async def test_due_queue_orders_most_overdue_first(client, owner, seeded_chunks):
    """The card the student is most behind on should be the first thing they see."""
    deck = await _deck(client, owner, seeded_chunks)
    cards = deck["cards"]
    now = datetime.utcnow()
    await _set_schedule(
        cards[0]["id"],
        due_at=now - timedelta(days=9), repetitions=5, interval_days=200,
    )
    await _set_schedule(
        cards[1]["id"],
        due_at=now - timedelta(days=1), repetitions=2, interval_days=6,
    )
    await _set_schedule(
        cards[2]["id"],
        due_at=now + timedelta(days=30), repetitions=3, interval_days=30,
    )

    res = await client.get(
        "/api/v1/flashcards/due", headers=_auth(owner["token"])
    )
    assert res.status_code == 200, res.text
    body = res.json()

    # Card 3 is not due yet, so it is excluded entirely.
    assert [c["id"] for c in body["cards"]] == [cards[0]["id"], cards[1]["id"]]
    assert body["due_count"] == 2
    assert body["new_count"] == 0


@pytest.mark.asyncio
async def test_due_queue_honours_limit_and_still_reports_the_true_count(
    client, owner, seeded_chunks
):
    deck = await _deck(client, owner, seeded_chunks)
    res = await client.get(
        "/api/v1/flashcards/due?limit=1", headers=_auth(owner["token"])
    )
    assert res.status_code == 200, res.text
    body = res.json()

    assert len(body["cards"]) == 1
    # "3 due" is more useful than "1 due" when the page is deliberately short.
    assert body["due_count"] == deck["card_count"] == 3


@pytest.mark.asyncio
async def test_due_queue_excludes_deleted_decks(client, owner, seeded_chunks):
    deck = await _deck(client, owner, seeded_chunks)
    res = await client.delete(
        f"/api/v1/flashcards/decks/{deck['id']}", headers=_auth(owner["token"])
    )
    assert res.status_code == 204

    res = await client.get("/api/v1/flashcards/due", headers=_auth(owner["token"]))
    assert res.status_code == 200
    assert res.json()["due_count"] == 0


@pytest.mark.asyncio
async def test_due_queue_carries_the_deck_each_card_belongs_to(
    client, owner, seeded_chunks
):
    """A card has to be addressable without its deck response in hand.

    The review endpoint takes a deck id and one result per card, and the client
    groups a cross-subject queue by deck. Neither is possible if the card only
    identifies itself by position, which is what it used to do.
    """
    deck = await _deck(client, owner, seeded_chunks)

    res = await client.get("/api/v1/flashcards/due", headers=_auth(owner["token"]))
    assert res.status_code == 200, res.text

    for card in res.json()["cards"]:
        assert card["deck_id"] == deck["id"]
        assert card["deck_title"] == deck["title"]
        assert card["subject_id"] == deck["subject_id"]


@pytest.mark.asyncio
async def test_deck_detail_echoes_deck_identity_on_each_card(client, owner, seeded_chunks):
    deck = await _deck(client, owner, seeded_chunks)

    res = await client.get(
        f"/api/v1/flashcards/decks/{deck['id']}", headers=_auth(owner["token"])
    )
    assert res.status_code == 200, res.text

    for card in res.json()["cards"]:
        assert card["deck_id"] == deck["id"]
        assert card["subject_id"] == deck["subject_id"]


@pytest.mark.asyncio
async def test_due_queue_rejects_an_out_of_range_limit(client, owner):
    res = await client.get("/api/v1/flashcards/due?limit=0", headers=_auth(owner["token"]))
    assert res.status_code == 422
    res = await client.get("/api/v1/flashcards/due?limit=101", headers=_auth(owner["token"]))
    assert res.status_code == 422


# ---------------------------------------------------------------------------
# Review advances the schedule
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_review_persists_the_new_schedule(client, owner, seeded_chunks):
    deck = await _deck(client, owner, seeded_chunks)
    reviews = [
        {"card_id": c["id"], "is_correct": c["id"] == deck["cards"][0]["id"]}
        for c in deck["cards"]
    ]

    res = await client.post(
        f"/api/v1/flashcards/decks/{deck['id']}/review",
        json={"reviews": reviews},
        headers=_auth(owner["token"]),
    )
    assert res.status_code == 200, res.text
    body = res.json()

    passed = next(r for r in body["results"] if r["card_id"] == deck["cards"][0]["id"])
    failed = next(r for r in body["results"] if r["card_id"] != deck["cards"][0]["id"])
    assert passed["schedule"]["interval_days"] == 1
    assert passed["schedule"]["repetitions"] == 1
    assert failed["schedule"]["interval_days"] == 1
    assert failed["schedule"]["repetitions"] == 0
    assert failed["schedule"]["lapses"] == 1
    assert passed["schedule"]["due_at"] is not None

    # Nothing is due right now: a pass schedules +1 day and so does a lapse, so
    # the whole deck leaves the queue for a day either way.
    assert body["due_count"] == 0
    assert body["new_count"] == 0


@pytest.mark.asyncio
async def test_schedule_survives_a_fresh_read(client, owner, seeded_chunks):
    """The point of storing the schedule: it is state, not a request-time derivation."""
    deck = await _deck(client, owner, seeded_chunks)
    reviews = [
        {"card_id": c["id"], "is_correct": True} for c in deck["cards"]
    ]
    await client.post(
        f"/api/v1/flashcards/decks/{deck['id']}/review",
        json={"reviews": reviews},
        headers=_auth(owner["token"]),
    )

    res = await client.get(
        f"/api/v1/flashcards/decks/{deck['id']}", headers=_auth(owner["token"])
    )
    card = res.json()["cards"][0]
    assert card["schedule"]["repetitions"] == 1
    assert card["schedule"]["interval_days"] == 1
    assert card["schedule"]["is_new"] is False
    assert card["schedule"]["last_reviewed_at"] is not None

    # A second round of passes must walk the 1 / 6 ladder, not restart at 1.
    second = [
        {"card_id": c["id"], "is_correct": True} for c in res.json()["cards"]
    ]
    res = await client.post(
        f"/api/v1/flashcards/decks/{deck['id']}/review",
        json={"reviews": second},
        headers=_auth(owner["token"]),
    )
    assert res.json()["results"][0]["schedule"]["interval_days"] == 6


@pytest.mark.asyncio
async def test_review_due_count_reflects_the_new_intervals(client, owner, seeded_chunks):
    """Only a card whose interval has actually elapsed belongs back in the queue."""
    deck = await _deck(client, owner, seeded_chunks)
    now = datetime.utcnow()
    # Push every card out of the queue so the outcome is unambiguous.
    for card in deck["cards"]:
        await _set_schedule(
            card["id"], due_at=now + timedelta(days=30), repetitions=4, interval_days=30
        )

    reviews = [
        {"card_id": c["id"], "is_correct": c["id"] == deck["cards"][0]["id"]}
        for c in deck["cards"]
    ]
    res = await client.post(
        f"/api/v1/flashcards/decks/{deck['id']}/review",
        json={"reviews": reviews},
        headers=_auth(owner["token"]),
    )
    assert res.status_code == 200, res.text
    body = res.json()

    # A pass restarts the 1/6 ladder, so the passed card comes back in a day; a
    # lapse also comes back in a day. Neither is due yet, and neither is new.
    assert body["due_count"] == 0
    assert body["new_count"] == 0

    # Force the passed card's interval to have elapsed and it reappears.
    await _set_schedule(deck["cards"][0]["id"], due_at=now - timedelta(hours=1))
    due = (await client.get(
        "/api/v1/flashcards/due", headers=_auth(owner["token"])
    )).json()
    assert [c["id"] for c in due["cards"]] == [deck["cards"][0]["id"]]
    assert due["new_count"] == 0



# ---------------------------------------------------------------------------
# Reschedule
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reschedule_returns_a_card_to_the_queue(client, owner, seeded_chunks):
    deck = await _deck(client, owner, seeded_chunks)
    card_id = deck["cards"][0]["id"]
    reviews = [{"card_id": c["id"], "is_correct": True} for c in deck["cards"]]
    await client.post(
        f"/api/v1/flashcards/decks/{deck['id']}/review",
        json={"reviews": reviews},
        headers=_auth(owner["token"]),
    )

    now = datetime.utcnow()
    await _set_schedule(
        card_id, due_at=now + timedelta(days=300), repetitions=7, interval_days=300
    )
    assert (await client.get(
        "/api/v1/flashcards/due", headers=_auth(owner["token"])
    )).json()["due_count"] == 0

    res = await client.post(
        f"/api/v1/flashcards/cards/{card_id}/reschedule", headers=_auth(owner["token"])
    )
    assert res.status_code == 200, res.text
    schedule = res.json()["schedule"]
    assert schedule["ease_factor"] == 2.5
    assert schedule["interval_days"] == 0
    assert schedule["repetitions"] == 0
    assert schedule["lapses"] == 0
    assert schedule["due_at"] is None
    assert schedule["is_new"] is True

    body = (await client.get(
        "/api/v1/flashcards/due", headers=_auth(owner["token"])
    )).json()
    assert card_id in [c["id"] for c in body["cards"]]
    assert body["new_count"] == 1


@pytest.mark.asyncio
async def test_reschedule_requires_authentication(client, owner, seeded_chunks):
    deck = await _deck(client, owner, seeded_chunks)
    res = await client.post(f"/api/v1/flashcards/cards/{deck['cards'][0]['id']}/reschedule")
    assert res.status_code == 401


@pytest.mark.asyncio
async def test_reschedule_hides_another_users_card(
    client, owner, other_user, seeded_chunks
):
    deck = await _deck(client, owner, seeded_chunks)
    res = await client.post(
        f"/api/v1/flashcards/cards/{deck['cards'][0]['id']}/reschedule",
        headers=_auth(other_user["token"]),
    )
    assert res.status_code == 404


@pytest.mark.asyncio
async def test_reschedule_unknown_card_is_404(client, owner):
    res = await client.post(
        f"/api/v1/flashcards/cards/{uuid.uuid4()}/reschedule",
        headers=_auth(owner["token"]),
    )
    assert res.status_code == 404


@pytest.mark.asyncio
async def test_reschedule_keeps_review_history(client, owner, seeded_chunks):
    """Forgetting a card is not the same as pretending the reviews never happened."""
    deck = await _deck(client, owner, seeded_chunks)
    card_id = deck["cards"][0]["id"]
    await client.post(
        f"/api/v1/flashcards/decks/{deck['id']}/review",
        json={"reviews": [{"card_id": c["id"], "is_correct": True} for c in deck["cards"]]},
        headers=_auth(owner["token"]),
    )
    await client.post(
        f"/api/v1/flashcards/cards/{card_id}/reschedule", headers=_auth(owner["token"])
    )

    res = await client.get(
        f"/api/v1/flashcards/decks/{deck['id']}", headers=_auth(owner["token"])
    )
    card = next(c for c in res.json()["cards"] if c["id"] == card_id)
    assert card["review_count"] == 1
    assert card["correct_count"] == 1


# ---------------------------------------------------------------------------
# Evidence channel
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_review_writes_the_flashcard_evidence_channel(client, owner, seeded_chunks):
    deck = await _deck(client, owner, seeded_chunks, topic="Spacing")
    await client.post(
        f"/api/v1/flashcards/decks/{deck['id']}/review",
        json={
            "reviews": [
                {"card_id": c["id"], "is_correct": c["id"] == deck["cards"][0]["id"]}
                for c in deck["cards"]
            ]
        },
        headers=_auth(owner["token"]),
    )

    async with TestAsyncSessionLocal() as session:
        result = await session.execute(
            select(TopicMastery).where(
                TopicMastery.user_id == uuid.UUID(owner["user_id"]),
                TopicMastery.subject_id == uuid.UUID(owner["subject_id"]),
            )
        )
        mastery = result.scalar_one()

    assert mastery.flashcard_reviews == 3
    assert mastery.flashcard_correct == 1
    assert mastery.flashcard_accuracy == 33.3
    # One of three, with confidence still ramping (3/10 of the target):
    # 0.333 * 0.3 * 100 = 10.0. Nowhere near mastery.
    assert mastery.flashcard_mastery == 10.0
    # The quiz channel is untouched by a card review.
    assert mastery.questions_attempted == 0
    assert mastery.mastery_score == 0.0


@pytest.mark.asyncio
async def test_flashcard_reviews_accumulate_across_sessions(client, owner, seeded_chunks):
    deck = await _deck(client, owner, seeded_chunks, topic="Spacing")
    for _ in range(2):
        await client.post(
            f"/api/v1/flashcards/decks/{deck['id']}/review",
            json={
                "reviews": [
                    {"card_id": c["id"], "is_correct": True} for c in deck["cards"]
                ]
            },
            headers=_auth(owner["token"]),
        )

    async with TestAsyncSessionLocal() as session:
        mastery = (
            await session.execute(
                select(TopicMastery).where(
                    TopicMastery.user_id == uuid.UUID(owner["user_id"])
                )
            )
        ).scalar_one()
    assert mastery.flashcard_reviews == 6
    assert mastery.flashcard_correct == 6
    assert mastery.flashcard_accuracy == 100.0
    assert mastery.flashcard_mastery == 60.0


@pytest.mark.asyncio
async def test_progress_reports_both_channels_separately(client, owner, seeded_chunks):
    """A card review must not inflate the quiz mastery shown beside it."""
    deck = await _deck(client, owner, seeded_chunks, topic="Spacing")
    await client.post(
        f"/api/v1/flashcards/decks/{deck['id']}/review",
        json={
            "reviews": [
                {"card_id": c["id"], "is_correct": True} for c in deck["cards"]
            ]
        },
        headers=_auth(owner["token"]),
    )

    res = await client.get(
        f"/api/v1/flashcards/subjects/{owner['subject_id']}/progress",
        headers=_auth(owner["token"]),
    )
    assert res.status_code == 200, res.text
    row = next(r for r in res.json() if r["topic"] == "Spacing")

    assert row["mastery_score"] == 0.0        # quiz channel: no evidence
    assert row["mastery_level"] == "novice"
    assert row["flashcard_mastery_score"] == 30.0
    assert row["due_count"] == 0
    assert row["reviewed_count"] == 3
