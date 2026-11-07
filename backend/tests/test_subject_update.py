"""`PATCH /api/v1/subjects/{subject_id}`.

The point of a PATCH is that omitted fields survive, so the tests check both
that a field actually changes and that its neighbours are left alone. Soft-delete
must still win: a deleted subject stays invisible to updates.
"""

import uuid

import pytest

from app.schemas.subject import SubjectUpdate


@pytest.mark.asyncio
async def test_patch_updates_a_subject(client, registered_user, subject):
    res = await client.patch(
        f"/api/v1/subjects/{subject}",
        json={"title": "  Distributed Systems  ", "color": "#dc2626"},
        headers=registered_user["headers"],
    )
    assert res.status_code == 200
    body = res.json()
    assert body["id"] == subject
    assert body["title"] == "Distributed Systems"
    assert body["color"] == "#dc2626"


@pytest.mark.asyncio
async def test_patch_leaves_omitted_fields_untouched(client, registered_user, subject):
    original = await client.get(
        f"/api/v1/subjects/{subject}", headers=registered_user["headers"]
    )
    assert original.json()["description"] == "Vector search notes"
    assert original.json()["color"] == "#0284c7"

    renamed = await client.patch(
        f"/api/v1/subjects/{subject}",
        json={"title": "Renamed Only"},
        headers=registered_user["headers"],
    )
    assert renamed.status_code == 200
    assert renamed.json()["title"] == "Renamed Only"
    assert renamed.json()["description"] == "Vector search notes"
    assert renamed.json()["color"] == "#0284c7"

    described = await client.patch(
        f"/api/v1/subjects/{subject}",
        json={"description": "  Now with notes  "},
        headers=registered_user["headers"],
    )
    assert described.json()["description"] == "Now with notes"
    assert described.json()["title"] == "Renamed Only"


@pytest.mark.asyncio
async def test_patch_with_empty_body_is_a_no_op(client, registered_user, subject):
    before = await client.get(
        f"/api/v1/subjects/{subject}", headers=registered_user["headers"]
    )

    res = await client.patch(
        f"/api/v1/subjects/{subject}", json={}, headers=registered_user["headers"]
    )
    assert res.status_code == 200
    assert res.json() == before.json()


@pytest.mark.asyncio
async def test_patch_rejects_blank_values(client, registered_user, subject):
    blank_title = await client.patch(
        f"/api/v1/subjects/{subject}",
        json={"title": "   "},
        headers=registered_user["headers"],
    )
    assert blank_title.status_code == 400
    assert "title" in blank_title.json()["detail"].lower()

    blank_color = await client.patch(
        f"/api/v1/subjects/{subject}",
        json={"color": ""},
        headers=registered_user["headers"],
    )
    assert blank_color.status_code == 400

    # A rejected patch changes nothing.
    unchanged = await client.get(
        f"/api/v1/subjects/{subject}", headers=registered_user["headers"]
    )
    assert unchanged.json()["title"] == "Retrieval Strategies"


@pytest.mark.asyncio
async def test_patch_ignores_unknown_fields(client, registered_user, subject):
    res = await client.patch(
        f"/api/v1/subjects/{subject}",
        json={"title": "Kept Clean", "is_admin": True},
        headers=registered_user["headers"],
    )
    assert res.status_code == 200
    assert res.json()["title"] == "Kept Clean"
    assert "is_admin" not in res.json()


@pytest.mark.asyncio
async def test_patch_is_owner_scoped(
    client, registered_user, other_user, subject, forget_session
):
    denied = await client.patch(
        f"/api/v1/subjects/{subject}",
        json={"title": "Hijacked"},
        headers=other_user["headers"],
    )
    assert denied.status_code == 404

    forget_session(client)
    anonymous = await client.patch(
        f"/api/v1/subjects/{subject}", json={"title": "Anonymous"}
    )
    assert anonymous.status_code == 401

    untouched = await client.get(
        f"/api/v1/subjects/{subject}", headers=registered_user["headers"]
    )
    assert untouched.json()["title"] == "Retrieval Strategies"


@pytest.mark.asyncio
async def test_patch_cannot_revive_a_deleted_subject(client, registered_user, subject):
    deleted = await client.delete(
        f"/api/v1/subjects/{subject}", headers=registered_user["headers"]
    )
    assert deleted.status_code == 204

    res = await client.patch(
        f"/api/v1/subjects/{subject}",
        json={"title": "Back From The Dead"},
        headers=registered_user["headers"],
    )
    assert res.status_code == 404


@pytest.mark.asyncio
async def test_patch_unknown_subject_is_404(client, registered_user):
    res = await client.patch(
        f"/api/v1/subjects/{uuid.uuid4()}",
        json={"title": "Nowhere"},
        headers=registered_user["headers"],
    )
    assert res.status_code == 404


def test_subject_update_schema_covers_the_editable_fields():
    """The schema is the contract the frontend types are written against."""

    assert set(SubjectUpdate.model_fields) == {"title", "description", "color"}
    assert SubjectUpdate(title="Only a title").model_dump(exclude_unset=True) == {
        "title": "Only a title"
    }
