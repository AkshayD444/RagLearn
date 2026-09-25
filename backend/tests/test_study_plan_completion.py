"""Regression coverage for study-plan item completion.

The contract is narrow but easy to break:

* ``completed=true``  -> ``completed`` is ``True``  and ``completed_at`` is set,
* ``completed=false`` -> ``completed`` is ``False`` and ``completed_at`` is NULL.

The timestamp must round-trip through the database, and the POST alias of the
completion route must behave exactly like the PATCH route.
"""

import uuid
from datetime import datetime, timedelta

import pytest
from sqlalchemy import select

from app.models.study_plan import StudyPlanItem
from tests.conftest import TestAsyncSessionLocal
from tests.test_study_plans import (
    _auth,
    _create_subject,
    _generate,
    _items,
    _register,
    _seed_material,
    _seed_topic_history,
)


async def _stored_item(item_id: str) -> StudyPlanItem:
    async with TestAsyncSessionLocal() as session:
        result = await session.execute(
            select(StudyPlanItem).where(StudyPlanItem.id == uuid.UUID(item_id))
        )
        return result.scalar_one()


async def _one_item_plan(client, label: str, title: str, topic: str) -> tuple[str, str, dict]:
    """Registers a user, seeds one weak topic, and returns ``(token, subject_id, item)``."""
    token, user_id = await _register(client, f"{label}-{uuid.uuid4().hex[:8]}@example.com")
    subject_id = await _create_subject(client, token, title)
    now = datetime.utcnow()
    await _seed_material(user_id, subject_id)
    await _seed_topic_history(
        user_id, subject_id, topic, [(False, now - timedelta(hours=1))] * 4
    )
    generated = await _generate(client, token, subject_id)
    assert generated.status_code == 201, generated.text
    items = _items(generated.json())
    assert len(items) == 1
    return token, subject_id, items[0]


@pytest.mark.asyncio
async def test_completed_true_sets_completed_at(client):
    token, subject_id, item = await _one_item_plan(
        client, "plan-complete-true", "Completion True Subject", "CompletionTopic"
    )

    stored_before = await _stored_item(item["id"])
    assert stored_before.completed is False
    assert stored_before.completed_at is None

    response = await client.patch(
        f"/api/v1/study-plans/items/{item['id']}/complete",
        json={"completed": True},
        headers=_auth(token),
    )
    assert response.status_code == 200
    body = response.json()
    updated = next(c for c in _items(body) if c["id"] == item["id"])
    assert updated["completed"] is True
    assert updated["completed_at"] is not None
    datetime.fromisoformat(updated["completed_at"])

    # The change is persisted, not just echoed back.
    before_update = datetime.utcnow()
    stored_after = await _stored_item(item["id"])
    assert stored_after.completed is True
    assert stored_after.completed_at is not None
    assert before_update - timedelta(minutes=5) <= stored_after.completed_at <= datetime.utcnow()

    # Re-reading the plan reports the same state.
    latest = await client.get(
        f"/api/v1/study-plans/subjects/{subject_id}", headers=_auth(token)
    )
    assert latest.status_code == 200
    refetched = next(c for c in _items(latest.json()) if c["id"] == item["id"])
    assert refetched["completed"] is True
    assert refetched["completed_at"] == updated["completed_at"]


@pytest.mark.asyncio
async def test_completed_false_clears_completed_at(client):
    token, _, item = await _one_item_plan(
        client, "plan-complete-false", "Completion False Subject", "CompletionTopic"
    )

    marked = await client.patch(
        f"/api/v1/study-plans/items/{item['id']}/complete",
        json={"completed": True},
        headers=_auth(token),
    )
    assert marked.status_code == 200
    assert (await _stored_item(item["id"])).completed_at is not None

    cleared = await client.patch(
        f"/api/v1/study-plans/items/{item['id']}/complete",
        json={"completed": False},
        headers=_auth(token),
    )
    assert cleared.status_code == 200
    updated = next(c for c in _items(cleared.json()) if c["id"] == item["id"])
    assert updated["completed"] is False
    assert updated["completed_at"] is None

    stored_after = await _stored_item(item["id"])
    assert stored_after.completed is False
    assert stored_after.completed_at is None


@pytest.mark.asyncio
async def test_completion_is_idempotent(client):
    token, _, item = await _one_item_plan(
        client, "plan-complete-idem", "Idempotent Subject", "IdempotentTopic"
    )

    first = await client.patch(
        f"/api/v1/study-plans/items/{item['id']}/complete",
        json={"completed": True},
        headers=_auth(token),
    )
    second = await client.patch(
        f"/api/v1/study-plans/items/{item['id']}/complete",
        json={"completed": True},
        headers=_auth(token),
    )
    assert first.status_code == second.status_code == 200

    first_item = next(c for c in _items(first.json()) if c["id"] == item["id"])
    second_item = next(c for c in _items(second.json()) if c["id"] == item["id"])
    assert first_item["completed"] is True
    assert second_item["completed"] is True
    assert second_item["completed_at"] is not None


@pytest.mark.asyncio
async def test_completion_toggle_round_trip(client):
    """A true/false/true cycle leaves a single consistent final state."""
    token, _, item = await _one_item_plan(
        client, "plan-toggle", "Toggle Subject", "ToggleTopic"
    )

    states = []
    for completed in (True, False, True):
        response = await client.patch(
            f"/api/v1/study-plans/items/{item['id']}/complete",
            json={"completed": completed},
            headers=_auth(token),
        )
        assert response.status_code == 200
        states.append(next(c for c in _items(response.json()) if c["id"] == item["id"]))

    assert [s["completed"] for s in states] == [True, False, True]
    assert states[0]["completed_at"] is not None
    assert states[1]["completed_at"] is None
    assert states[2]["completed_at"] is not None

    stored = await _stored_item(item["id"])
    assert stored.completed is True
    assert stored.completed_at is not None


@pytest.mark.asyncio
async def test_post_alias_completes_an_item_like_patch(client):
    """The ``POST /items/{id}/complete`` alias shares the same semantics."""
    token, _, item = await _one_item_plan(
        client, "plan-post-alias", "Post Alias Subject", "PostAliasTopic"
    )

    completed = await client.post(
        f"/api/v1/study-plans/items/{item['id']}/complete",
        json={"completed": True},
        headers=_auth(token),
    )
    assert completed.status_code == 200
    assert (await _stored_item(item["id"])).completed_at is not None

    cleared = await client.post(
        f"/api/v1/study-plans/items/{item['id']}/complete",
        json={"completed": False},
        headers=_auth(token),
    )
    assert cleared.status_code == 200
    assert (await _stored_item(item["id"])).completed_at is None


@pytest.mark.asyncio
async def test_completion_defaults_to_true_when_the_body_is_omitted(client):
    """``payload=None`` means "mark it complete"."""
    token, _, item = await _one_item_plan(
        client, "plan-no-body", "No Body Subject", "NoBodyTopic"
    )

    response = await client.post(
        f"/api/v1/study-plans/items/{item['id']}/complete", headers=_auth(token)
    )
    assert response.status_code == 200
    updated = next(c for c in _items(response.json()) if c["id"] == item["id"])
    assert updated["completed"] is True
    assert updated["completed_at"] is not None


@pytest.mark.asyncio
async def test_completion_rejects_a_non_boolean_payload(client):
    token, _, item = await _one_item_plan(
        client, "plan-bad-bool", "Bad Bool Subject", "BadBoolTopic"
    )

    response = await client.patch(
        f"/api/v1/study-plans/items/{item['id']}/complete",
        json={"completed": "yes please"},
        headers=_auth(token),
    )
    assert response.status_code == 422
    assert (await _stored_item(item["id"])).completed_at is None


@pytest.mark.asyncio
async def test_completing_one_item_leaves_the_others_untouched(client):
    token, user_id = await _register(
        client, f"plan-one-item-{uuid.uuid4().hex[:8]}@example.com"
    )
    subject_id = await _create_subject(client, token, "One Item Subject")
    now = datetime.utcnow()
    await _seed_material(user_id, subject_id)
    for topic in ("FirstTopic", "SecondTopic"):
        await _seed_topic_history(
            user_id, subject_id, topic, [(False, now - timedelta(hours=1))] * 4
        )

    generated = await _generate(
        client, token, subject_id, requested_sessions=1, max_topics_per_session=2
    )
    assert generated.status_code == 201
    items = _items(generated.json())
    assert len(items) == 2

    target = items[0]
    response = await client.patch(
        f"/api/v1/study-plans/items/{target['id']}/complete",
        json={"completed": True},
        headers=_auth(token),
    )
    assert response.status_code == 200

    by_id = {i["id"]: i for i in _items(response.json())}
    assert by_id[target["id"]]["completed"] is True
    assert by_id[target["id"]]["completed_at"] is not None
    assert by_id[items[1]["id"]]["completed"] is False
    assert by_id[items[1]["id"]]["completed_at"] is None

    assert (await _stored_item(items[1]["id"])).completed is False
    assert (await _stored_item(items[1]["id"])).completed_at is None


@pytest.mark.asyncio
async def test_completion_survives_a_different_read_route(client):
    """Completion state is stored on the plan, not held in the request."""
    token, subject_id, item = await _one_item_plan(
        client, "plan-reload", "Reload Subject", "ReloadTopic"
    )

    await client.patch(
        f"/api/v1/study-plans/items/{item['id']}/complete",
        json={"completed": True},
        headers=_auth(token),
    )

    latest = await client.get("/api/v1/study-plans/latest", headers=_auth(token))
    assert latest.status_code == 200
    found = next(c for c in _items(latest.json()) if c["id"] == item["id"])
    assert found["completed"] is True
    assert found["completed_at"] is not None

    filtered = await client.get(
        f"/api/v1/study-plans/latest?subject_id={subject_id}", headers=_auth(token)
    )
    assert filtered.status_code == 200
    assert next(c for c in _items(filtered.json()) if c["id"] == item["id"])["completed"] is True

    by_subject = await client.get(
        f"/api/v1/study-plans/subjects/{subject_id}/latest", headers=_auth(token)
    )
    assert by_subject.status_code == 200
    found = next(c for c in _items(by_subject.json()) if c["id"] == item["id"])
    assert found["completed"] is True
