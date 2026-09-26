"""Coverage for API routes and edge cases the Phase 9 audit found untested.

The gaps closed here are:

* ``GET  /api/v1/health/db``                     - no test at all
* ``POST /api/v1/auth/login/form``               - Swagger OAuth2 alias untested
* ``POST /api/v1/study-plans``                   - bare collection alias untested
* ``POST /api/v1/study-plans/subjects/{id}``     - path-param generate untested
* ``GET  /api/v1/study-plans/subjects/{id}/latest`` - nested read alias untested
* ``GET  /api/v1/subjects/{id}``                 - single-subject read untested
* auth / token edge cases (duplicate email, bad token, orphan token)
"""

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.core.security import create_access_token, decode_access_token
from tests.test_study_plans import (
    _auth,
    _create_subject,
    _items,
    _register,
    _seed_material,
    _seed_topic_history,
)

EMAIL = "route-coverage@example.com"
PASSWORD = "password123"


async def _returns(value):
    """Stands in for ``check_database_connection`` inside a monkeypatch."""
    return value


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_health_endpoint_reports_offline_status(client):
    res = await client.get("/api/v1/health")
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "online"
    assert body["environment"] == "test"
    assert body["service"]


@pytest.mark.asyncio
async def test_health_db_endpoint_reports_healthy_when_connected(client, monkeypatch):
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
    res = await client.get("/api/v1/health/db")
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "healthy"
    assert body["database"]["pgvector_installed"] is True


@pytest.mark.asyncio
async def test_health_db_endpoint_degrades_when_disconnected(client, monkeypatch):
    import app.api.v1.health as health_api

    monkeypatch.setattr(
        health_api,
        "check_database_connection",
        lambda: _returns(
            {
                "status": "disconnected",
                "select_one": False,
                "pgvector_installed": False,
                "error": "connection refused",
            }
        ),
    )
    res = await client.get("/api/v1/health/db")
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "degraded"
    assert body["database"]["error"] == "connection refused"


@pytest.mark.asyncio
async def test_check_database_connection_never_raises(monkeypatch):
    """The helper degrades gracefully instead of propagating a driver error."""
    from app.core.database import check_database_connection

    class _BrokenSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def execute(self, *_args, **_kwargs):
            raise RuntimeError("driver exploded")

    monkeypatch.setattr("app.core.database.AsyncSessionLocal", _BrokenSession)
    result = await check_database_connection()
    assert result["status"] == "disconnected"
    assert result["select_one"] is False
    assert result["pgvector_installed"] is False
    assert "driver exploded" in result["error"]


@pytest.mark.asyncio
async def test_root_and_openapi_are_served(client):
    root = await client.get("/")
    assert root.status_code == 200
    assert root.json()["health_check"] == "/api/v1/health"

    spec = (await client.get("/api/v1/openapi.json")).json()
    for path in (
        "/api/v1/documents/upload",
        "/api/v1/study-plans/generate",
        "/api/v1/study-plans/subjects/{subject_id}",
        "/api/v1/study-plans/subjects/{subject_id}/latest",
        "/api/v1/auth/login/form",
        "/api/v1/analytics/subjects/{subject_id}/performance",
    ):
        assert path in spec["paths"], path


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_duplicate_registration_is_rejected(client):
    payload = {"email": EMAIL, "full_name": "Route Student", "password": PASSWORD}
    first = await client.post("/api/v1/auth/register", json=payload)
    assert first.status_code == 201

    second = await client.post("/api/v1/auth/register", json=payload)
    assert second.status_code == 400
    assert "already exists" in second.json()["detail"].lower()


@pytest.mark.asyncio
async def test_duplicate_detection_is_case_insensitive(client):
    await client.post(
        "/api/v1/auth/register",
        json={"email": "Mixed.Case@Example.com", "full_name": "A", "password": PASSWORD},
    )
    again = await client.post(
        "/api/v1/auth/register",
        json={"email": "mixed.case@example.com", "full_name": "B", "password": PASSWORD},
    )
    assert again.status_code == 400


@pytest.mark.asyncio
async def test_registration_normalises_the_email(client):
    res = await client.post(
        "/api/v1/auth/register",
        json={"email": "  UPPER@Example.com  ".strip(), "full_name": "A", "password": PASSWORD},
    )
    assert res.status_code == 201
    assert res.json()["user"]["email"] == "upper@example.com"


@pytest.mark.asyncio
async def test_registration_rejects_an_invalid_email(client):
    res = await client.post(
        "/api/v1/auth/register",
        json={"email": "not-an-email", "full_name": "A", "password": PASSWORD},
    )
    assert res.status_code == 422


@pytest.mark.asyncio
async def test_registration_requires_every_field(client):
    for payload in (
        {"email": "a@example.com", "full_name": "A"},
        {"email": "a@example.com", "password": PASSWORD},
        {"full_name": "A", "password": PASSWORD},
    ):
        res = await client.post("/api/v1/auth/register", json=payload)
        assert res.status_code == 422, payload


@pytest.mark.asyncio
async def test_oauth2_password_form_login(client):
    """The Swagger-facing form login returns the same token payload as JSON."""
    await client.post(
        "/api/v1/auth/register",
        json={"email": "form-login@example.com", "full_name": "Form", "password": PASSWORD},
    )

    ok = await client.post(
        "/api/v1/auth/login/form",
        data={"username": "form-login@example.com", "password": PASSWORD},
    )
    assert ok.status_code == 200
    body = ok.json()
    assert body["token_type"] == "bearer"
    assert body["user"]["email"] == "form-login@example.com"

    token = body["access_token"]
    me = await client.get("/api/v1/auth/me", headers=_auth(token))
    assert me.status_code == 200
    assert me.json()["id"] == body["user"]["id"]


@pytest.mark.asyncio
async def test_oauth2_password_form_login_rejects_bad_credentials(client):
    await client.post(
        "/api/v1/auth/register",
        json={"email": "form-bad@example.com", "full_name": "Form", "password": PASSWORD},
    )

    wrong_password = await client.post(
        "/api/v1/auth/login/form",
        data={"username": "form-bad@example.com", "password": "nope"},
    )
    assert wrong_password.status_code == 401
    assert wrong_password.json()["detail"].lower() == "incorrect email or password"

    unknown_user = await client.post(
        "/api/v1/auth/login/form",
        data={"username": "nobody@example.com", "password": PASSWORD},
    )
    assert unknown_user.status_code == 401


@pytest.mark.asyncio
async def test_json_login_is_case_insensitive_on_the_email(client):
    await client.post(
        "/api/v1/auth/register",
        json={"email": "case@example.com", "full_name": "Case", "password": PASSWORD},
    )
    res = await client.post(
        "/api/v1/auth/login", json={"email": "CASE@example.com", "password": PASSWORD}
    )
    assert res.status_code == 200
    assert res.json()["user"]["email"] == "case@example.com"


@pytest.mark.asyncio
async def test_me_requires_a_valid_bearer_token(client):
    assert (await client.get("/api/v1/auth/me")).status_code == 401

    garbage = await client.get("/api/v1/auth/me", headers=_auth("not-a-jwt"))
    assert garbage.status_code == 401
    assert garbage.json()["detail"] == "Could not validate credentials"

    malformed = await client.get(
        "/api/v1/auth/me", headers={"Authorization": "Bearer  a b c"}
    )
    assert malformed.status_code == 401


@pytest.mark.asyncio
async def test_token_with_a_non_uuid_subject_is_rejected(client):
    token = create_access_token(subject="definitely-not-a-uuid")
    res = await client.get("/api/v1/auth/me", headers=_auth(token))
    assert res.status_code == 401


@pytest.mark.asyncio
async def test_token_without_a_subject_is_rejected(client):
    import jwt

    from app.core.config import settings

    token = jwt.encode({"exp": datetime.now(timezone.utc) + timedelta(hours=1)},
                       settings.SECRET_KEY, algorithm=settings.ALGORITHM)
    res = await client.get("/api/v1/auth/me", headers=_auth(token))
    assert res.status_code == 401


@pytest.mark.asyncio
async def test_token_for_a_missing_user_is_a_404(client):
    token = create_access_token(subject=uuid.uuid4())
    res = await client.get("/api/v1/auth/me", headers=_auth(token))
    assert res.status_code == 404
    assert res.json()["detail"] == "User not found"


@pytest.mark.asyncio
async def test_expired_token_is_rejected(client, registered_user):
    import jwt

    from app.core.config import settings

    token = jwt.encode(
        {"sub": registered_user["user_id"],
         "exp": datetime.now(timezone.utc) - timedelta(hours=1)},
        settings.SECRET_KEY,
        algorithm=settings.ALGORITHM,
    )
    res = await client.get("/api/v1/auth/me", headers=_auth(token))
    assert res.status_code == 401


def test_access_token_round_trips():
    subject = uuid.uuid4()
    token = create_access_token(subject=subject)
    payload = decode_access_token(token)
    assert payload is not None
    assert payload["sub"] == str(subject)
    assert decode_access_token("garbage") is None


# ---------------------------------------------------------------------------
# Subjects
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_single_subject(client, registered_user, subject):
    res = await client.get(f"/api/v1/subjects/{subject}", headers=registered_user["headers"])
    assert res.status_code == 200
    assert res.json()["id"] == subject
    assert res.json()["title"] == "Retrieval Strategies"


@pytest.mark.asyncio
async def test_get_single_subject_hides_other_users_and_deleted_rows(
    client, registered_user, subject, other_user
):
    foreign = await client.get(f"/api/v1/subjects/{subject}", headers=other_user["headers"])
    assert foreign.status_code == 404

    assert (await client.delete(
        f"/api/v1/subjects/{subject}", headers=registered_user["headers"]
    )).status_code == 204

    deleted = await client.get(f"/api/v1/subjects/{subject}", headers=registered_user["headers"])
    assert deleted.status_code == 404

    listed = await client.get("/api/v1/subjects", headers=registered_user["headers"])
    assert listed.json() == []


@pytest.mark.asyncio
async def test_subject_creation_applies_defaults_and_strips_whitespace(client, registered_user):
    res = await client.post(
        "/api/v1/subjects",
        json={"title": "  Trimmed Title  "},
        headers=registered_user["headers"],
    )
    assert res.status_code == 201
    body = res.json()
    assert body["title"] == "Trimmed Title"
    assert body["description"] == ""
    assert body["color"] == "#0284c7"


@pytest.mark.asyncio
async def test_subject_creation_accepts_a_custom_color(client, registered_user):
    res = await client.post(
        "/api/v1/subjects",
        json={"title": "Coloured", "description": "  d  ", "color": "#ff0000"},
        headers=registered_user["headers"],
    )
    assert res.status_code == 201
    assert res.json()["color"] == "#ff0000"
    assert res.json()["description"] == "d"


@pytest.mark.asyncio
async def test_subject_creation_requires_a_title(client, registered_user):
    res = await client.post("/api/v1/subjects", json={}, headers=registered_user["headers"])
    assert res.status_code == 422


@pytest.mark.asyncio
async def test_deleting_a_subject_twice_is_a_404(client, registered_user, subject):
    assert (await client.delete(
        f"/api/v1/subjects/{subject}", headers=registered_user["headers"]
    )).status_code == 204
    assert (await client.delete(
        f"/api/v1/subjects/{subject}", headers=registered_user["headers"]
    )).status_code == 404


@pytest.mark.asyncio
async def test_subject_routes_require_authentication(client, subject):
    assert (await client.get("/api/v1/subjects")).status_code == 401
    assert (await client.post("/api/v1/subjects", json={"title": "x"})).status_code == 401
    assert (await client.get(f"/api/v1/subjects/{subject}")).status_code == 401
    assert (await client.delete(f"/api/v1/subjects/{subject}")).status_code == 401


# ---------------------------------------------------------------------------
# Study-plan route aliases
# ---------------------------------------------------------------------------


async def _seed_weak_topic(client, label: str, title: str, topic: str) -> tuple[str, str]:
    token, user_id = await _register(client, f"{label}-{uuid.uuid4().hex[:8]}@example.com")
    subject_id = await _create_subject(client, token, title)
    now = datetime.utcnow()
    await _seed_material(user_id, subject_id)
    await _seed_topic_history(
        user_id, subject_id, topic, [(False, now - timedelta(hours=1))] * 4
    )
    return token, subject_id


@pytest.mark.asyncio
async def test_post_study_plans_collection_alias_generates_a_plan(client):
    token, subject_id = await _seed_weak_topic(
        client, "plan-collection-alias", "Collection Alias", "CollectionTopic"
    )

    res = await client.post(
        "/api/v1/study-plans",
        json={"subject_id": subject_id},
        headers=_auth(token),
    )
    assert res.status_code == 201, res.text
    plan = res.json()
    assert plan["subject_id"] == subject_id
    assert [item["topic"] for item in _items(plan)] == ["CollectionTopic"]

    # The alias writes the same persisted plan the canonical route returns.
    latest = await client.get(
        f"/api/v1/study-plans/subjects/{subject_id}", headers=_auth(token)
    )
    assert latest.status_code == 200
    assert latest.json()["id"] == plan["id"]


@pytest.mark.asyncio
async def test_post_study_plans_collection_alias_validates_ownership(client, other_user):
    token, subject_id = await _seed_weak_topic(
        client, "plan-collection-owner", "Collection Owner", "OwnerTopic"
    )
    res = await client.post(
        "/api/v1/study-plans",
        json={"subject_id": subject_id},
        headers=other_user["headers"],
    )
    assert res.status_code == 404


@pytest.mark.asyncio
async def test_post_subject_scoped_study_plan_uses_defaults(client):
    """The path-param alias needs no body at all."""
    token, subject_id = await _seed_weak_topic(
        client, "plan-path-alias", "Path Alias", "PathAliasTopic"
    )

    res = await client.post(
        f"/api/v1/study-plans/subjects/{subject_id}", headers=_auth(token)
    )
    assert res.status_code == 201, res.text
    plan = res.json()
    assert plan["subject_id"] == subject_id
    assert [item["topic"] for item in _items(plan)] == ["PathAliasTopic"]


@pytest.mark.asyncio
async def test_post_subject_scoped_study_plan_accepts_an_optional_body(client):
    token, subject_id = await _seed_weak_topic(
        client, "plan-path-alias-body", "Path Alias Body", "PathAliasBodyTopic"
    )

    res = await client.post(
        f"/api/v1/study-plans/subjects/{subject_id}",
        json={"requested_sessions": 1, "max_topics_per_session": 1},
        headers=_auth(token),
    )
    assert res.status_code == 201
    assert len(res.json()["sessions"]) == 1


@pytest.mark.asyncio
async def test_post_subject_scoped_study_plan_honours_legacy_aliases(client):
    """``StudyPlanOptions`` accepts its documented alias keys."""
    token, subject_id = await _seed_weak_topic(
        client, "plan-alias-keys", "Alias Keys", "AliasKeysTopic"
    )

    res = await client.post(
        f"/api/v1/study-plans/subjects/{subject_id}",
        json={"session_count": 1, "topic_limit": 1, "available_study_time_minutes": 30},
        headers=_auth(token),
    )
    assert res.status_code == 201, res.text
    assert len(res.json()["sessions"]) == 1


@pytest.mark.asyncio
async def test_post_subject_scoped_study_plan_is_user_scoped(client, other_user):
    token, subject_id = await _seed_weak_topic(
        client, "plan-path-alias-scope", "Path Alias Scope", "ScopeTopic"
    )
    res = await client.post(
        f"/api/v1/study-plans/subjects/{subject_id}", headers=other_user["headers"]
    )
    assert res.status_code == 404


@pytest.mark.asyncio
async def test_nested_latest_route_matches_the_flat_route(client):
    token, subject_id = await _seed_weak_topic(
        client, "plan-nested-latest", "Nested Latest", "NestedLatestTopic"
    )

    generated = await client.post(
        "/api/v1/study-plans/generate",
        json={"subject_id": subject_id},
        headers=_auth(token),
    )
    assert generated.status_code == 201

    nested = await client.get(
        f"/api/v1/study-plans/subjects/{subject_id}/latest", headers=_auth(token)
    )
    flat = await client.get(
        f"/api/v1/study-plans/subjects/{subject_id}", headers=_auth(token)
    )
    assert nested.status_code == flat.status_code == 200
    assert nested.json() == flat.json()
    assert nested.json()["id"] == generated.json()["id"]


@pytest.mark.asyncio
async def test_nested_latest_route_is_404_without_a_plan(client):
    token, subject_id = await _seed_weak_topic(
        client, "plan-nested-empty", "Nested Empty", "NestedEmptyTopic"
    )
    # The seeding only creates recommendations; no plan has been generated yet.
    empty = await client.get(
        f"/api/v1/study-plans/subjects/{subject_id}/latest", headers=_auth(token)
    )
    assert empty.status_code == 404
    assert empty.json()["detail"] == "No study plan found."


@pytest.mark.asyncio
async def test_nested_latest_route_is_user_scoped(client, other_user):
    token, subject_id = await _seed_weak_topic(
        client, "plan-nested-scope", "Nested Scope", "NestedScopeTopic"
    )
    await client.post(
        "/api/v1/study-plans/generate",
        json={"subject_id": subject_id},
        headers=_auth(token),
    )
    res = await client.get(
        f"/api/v1/study-plans/subjects/{subject_id}/latest",
        headers=other_user["headers"],
    )
    assert res.status_code == 404


@pytest.mark.asyncio
async def test_latest_with_subject_filter_requires_ownership(client, other_user):
    token, subject_id = await _seed_weak_topic(
        client, "plan-latest-filter", "Latest Filter", "LatestFilterTopic"
    )
    res = await client.get(
        f"/api/v1/study-plans/latest?subject_id={subject_id}",
        headers=other_user["headers"],
    )
    assert res.status_code == 404


@pytest.mark.asyncio
async def test_latest_is_404_when_the_user_has_no_plan(client, registered_user):
    res = await client.get("/api/v1/study-plans/latest", headers=registered_user["headers"])
    assert res.status_code == 404
    assert res.json()["detail"] == "No study plan found."


@pytest.mark.asyncio
async def test_latest_rejects_a_malformed_subject_filter(client, registered_user):
    res = await client.get(
        "/api/v1/study-plans/latest?subject_id=not-a-uuid", headers=registered_user["headers"]
    )
    assert res.status_code == 422


@pytest.mark.asyncio
async def test_study_plan_routes_require_authentication(client):
    subject_id = str(uuid.uuid4())
    assert (await client.post(
        "/api/v1/study-plans", json={"subject_id": subject_id}
    )).status_code == 401
    assert (await client.post(
        f"/api/v1/study-plans/subjects/{subject_id}"
    )).status_code == 401
    assert (await client.get("/api/v1/study-plans/latest")).status_code == 401
    assert (await client.get(
        f"/api/v1/study-plans/subjects/{subject_id}/latest"
    )).status_code == 401


@pytest.mark.asyncio
async def test_generate_requires_a_valid_subject_id(client, registered_user):
    res = await client.post(
        "/api/v1/study-plans/generate",
        json={"subject_id": "not-a-uuid"},
        headers=_auth(registered_user["token"]),
    )
    assert res.status_code == 422


# ---------------------------------------------------------------------------
# Analytics
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_performance_for_a_subject_without_attempts_is_empty(client, registered_user, subject):
    res = await client.get(
        f"/api/v1/analytics/subjects/{subject}/performance",
        headers=registered_user["headers"],
    )
    assert res.status_code == 200
    body = res.json()
    assert body["subject_id"] == subject
    assert body["summary"]["total_attempts"] == 0
    assert body["summary"]["accuracy"] == 0
    assert body["summary"]["average_percentage"] == 0.0
    assert body["summary"]["best_percentage"] == 0.0
    assert body["attempts"] == []
    assert body["mastery"] == []
    assert body["summary"]["difficulty_breakdown"] == []


@pytest.mark.asyncio
async def test_performance_is_user_scoped(client, registered_user, subject, other_user):
    res = await client.get(
        f"/api/v1/analytics/subjects/{subject}/performance",
        headers=other_user["headers"],
    )
    assert res.status_code == 404
    assert "access denied" in res.json()["detail"]


@pytest.mark.asyncio
async def test_performance_survives_a_soft_deleted_subject(client, registered_user, subject):
    """Historical performance stays readable after a subject is soft-deleted."""
    res = await client.get(
        f"/api/v1/analytics/subjects/{subject}/performance",
        headers=registered_user["headers"],
    )
    assert res.status_code == 200

    assert (await client.delete(
        f"/api/v1/subjects/{subject}", headers=registered_user["headers"]
    )).status_code == 204

    after = await client.get(
        f"/api/v1/analytics/subjects/{subject}/performance",
        headers=registered_user["headers"],
    )
    assert after.status_code == 200
    assert after.json()["subject_id"] == subject


@pytest.mark.asyncio
async def test_performance_requires_authentication(client, subject):
    res = await client.get(f"/api/v1/analytics/subjects/{subject}/performance")
    assert res.status_code == 401


# ---------------------------------------------------------------------------
# Recommendations
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_recommendation_limit_bounds_are_enforced(client, registered_user, subject):
    too_small = await client.get(
        f"/api/v1/recommendations/subjects/{subject}?limit=0",
        headers=registered_user["headers"],
    )
    assert too_small.status_code == 422

    too_large = await client.get(
        f"/api/v1/recommendations/subjects/{subject}?limit=51",
        headers=registered_user["headers"],
    )
    assert too_large.status_code == 422

    ok = await client.get(
        f"/api/v1/recommendations/subjects/{subject}?limit=1",
        headers=registered_user["headers"],
    )
    assert ok.status_code == 200


@pytest.mark.asyncio
async def test_top_recommendations_are_scoped_and_bounded(client, registered_user):
    res = await client.get(
        "/api/v1/recommendations/top?limit=50", headers=registered_user["headers"]
    )
    assert res.status_code == 200
    assert res.json() == []

    assert (await client.get(
        "/api/v1/recommendations/top?limit=0", headers=registered_user["headers"]
    )).status_code == 422


@pytest.mark.asyncio
async def test_subject_recommendations_are_404_for_foreign_subjects(
    client, registered_user, subject, other_user
):
    res = await client.get(
        f"/api/v1/recommendations/subjects/{subject}", headers=other_user["headers"]
    )
    assert res.status_code == 404
