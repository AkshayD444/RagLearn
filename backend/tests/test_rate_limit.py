"""Rate limiter tests.

Three layers are covered:

* ``SlidingWindowLimiter`` in isolation, using an injected clock so the window
  behaviour is deterministic rather than sleep-based;
* per-policy isolation -- the four policies share one dict with four different
  windows, so one policy must never expire or spend another's counters;
* the FastAPI dependency, through the real routes, to prove the limits are
  actually wired onto the endpoints that spend money;
* that a user-scoped policy is keyed by the *resolved user*, with the ordering
  established by the dependency graph rather than by route parameter order.
"""

import inspect
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import Depends, FastAPI, Header
from fastapi.params import Depends as DependsMarker
from fastapi.testclient import TestClient

from app.api.deps import get_current_user
from app.core.config import settings
from app.core.rate_limit import (
    GENERATIVE_POLICY,
    LOGIN_POLICY,
    REGISTER_POLICY,
    UPLOAD_POLICY,
    RateLimitBackend,
    RateLimitPolicy,
    SlidingWindowLimiter,
    describe_effective_limit_scope,
    email_identity,
    get_backend,
    limiter,
    rate_limit,
)


# ---------------------------------------------------------------------------
# SlidingWindowLimiter
# ---------------------------------------------------------------------------


def test_requests_within_the_budget_are_allowed():
    window = SlidingWindowLimiter()
    for i in range(3):
        assert window.hit("k", limit=3, window=60, now=0.0) is True, i


def test_requests_over_the_budget_are_blocked():
    window = SlidingWindowLimiter()
    for _ in range(3):
        window.hit("k", limit=3, window=60, now=0.0)

    assert window.hit("k", limit=3, window=60, now=0.0) is False
    # A blocked request must not extend the window (no sliding on rejection).
    assert window.hit("k", limit=3, window=60, now=0.0) is False


def test_budget_recovers_once_the_window_slides_past_the_oldest_hit():
    window = SlidingWindowLimiter()
    for _ in range(2):
        window.hit("k", limit=2, window=60, now=0.0)

    assert window.hit("k", limit=2, window=60, now=10.0) is False
    # 61s after the first hit, it has aged out of the 60s window.
    assert window.hit("k", limit=2, window=60, now=61.0) is True


def test_window_slides_rather_than_resetting_on_a_boundary():
    """A fixed window would hand out a whole extra budget at the boundary.

    Two hits at t=0 and one at t=1 exhaust a limit of 3. At t=60 a *fixed*
    window aligned to zero would reset completely and allow 3 more. A sliding
    window only expires the two that are genuinely 60s old, leaving one hit in
    memory and allowing 2.
    """
    window = SlidingWindowLimiter()
    window.hit("k", limit=3, window=60, now=0.0)
    window.hit("k", limit=3, window=60, now=0.0)
    window.hit("k", limit=3, window=60, now=1.0)

    assert window.hit("k", limit=3, window=60, now=60.0) is True
    assert window.hit("k", limit=3, window=60, now=60.0) is True
    # The t=1 hit is still inside the window, so the budget is spent.
    assert window.hit("k", limit=3, window=60, now=60.0) is False


def test_keys_are_independent():
    window = SlidingWindowLimiter()
    assert window.hit("a", limit=1, window=60, now=0.0) is True
    assert window.hit("b", limit=1, window=60, now=0.0) is True
    assert window.hit("a", limit=1, window=60, now=0.0) is False


def test_retry_after_is_at_least_one_second():
    """A 0 would invite an immediate retry storm."""
    window = SlidingWindowLimiter()
    window.hit("k", limit=1, window=60, now=0.0)

    assert window.retry_after("k", 60, now=0.0) >= 1
    assert window.retry_after("k", 60, now=59.0) >= 1
    # Unknown key: still a positive, retryable number.
    assert window.retry_after("never-seen", 60, now=0.0) >= 1


def test_key_count_is_bounded():
    """Rotating client addresses must not grow the dict without limit."""
    window = SlidingWindowLimiter(max_keys=10)
    for i in range(100):
        window.hit(f"key-{i}", limit=5, window=60, now=0.0)

    assert len(window._hits) <= 10


def test_expired_hits_stop_counting_towards_the_budget():
    """A key's own timestamps age out; the key object itself may linger.

    Pruning of the key entry is best-effort and window-dependent, so the
    guarantee worth asserting is the behavioural one: an idle key gets its full
    budget back. Memory reclamation is covered by the LRU cap test above.
    """
    window = SlidingWindowLimiter()
    assert window.hit("k", limit=1, window=60, now=0.0) is True
    assert window.hit("k", limit=1, window=60, now=1.0) is False
    assert window.hit("k", limit=1, window=60, now=120.0) is True


def test_reset_clears_everything():
    window = SlidingWindowLimiter()
    window.hit("k", limit=1, window=60, now=0.0)
    window.reset()

    assert window.hit("k", limit=1, window=60, now=0.0) is True


# ---------------------------------------------------------------------------
# Policy configuration
# ---------------------------------------------------------------------------


def test_policies_are_configured_from_settings():
    assert LOGIN_POLICY.limit == settings.RATE_LIMIT_LOGIN_ATTEMPTS
    assert REGISTER_POLICY.limit == settings.RATE_LIMIT_REGISTER_ATTEMPTS
    assert GENERATIVE_POLICY.limit == settings.RATE_LIMIT_GENERATIVE_ATTEMPTS
    assert UPLOAD_POLICY.limit == settings.RATE_LIMIT_UPLOAD_ATTEMPTS


def test_limits_are_enabled_and_positive():
    assert settings.RATE_LIMIT_ENABLED is True
    for policy in (LOGIN_POLICY, REGISTER_POLICY, GENERATIVE_POLICY, UPLOAD_POLICY):
        assert policy.limit > 0, policy.name
        assert policy.window > 0, policy.name


def test_auth_policies_are_not_keyed_by_user():
    """Login and register are unauthenticated, so there is no user to key on."""
    assert LOGIN_POLICY.key_by_user is False
    assert REGISTER_POLICY.key_by_user is False
    # Authenticated, spend-bearing routes are keyed by user so that a shared
    # NAT does not pool unrelated people's budgets.
    assert GENERATIVE_POLICY.key_by_user is True
    assert UPLOAD_POLICY.key_by_user is True


# ---------------------------------------------------------------------------
# Through the real routes
# ---------------------------------------------------------------------------


@pytest.fixture
def small_login_budget(monkeypatch):
    """Shrinks the login budget so the test does not need 10 requests."""
    monkeypatch.setattr(LOGIN_POLICY, "limit", 3)
    limiter.reset()
    yield
    limiter.reset()


@pytest.mark.asyncio
async def test_login_is_throttled(client, registered_user, small_login_budget):
    payload = {"email": registered_user["email"], "password": "password123"}

    for _ in range(3):
        assert (await client.post("/api/v1/auth/login", json=payload)).status_code == 200

    blocked = await client.post("/api/v1/auth/login", json=payload)
    assert blocked.status_code == 429
    assert blocked.json()["detail"]
    assert int(blocked.headers["Retry-After"]) >= 1
    assert blocked.headers["X-RateLimit-Policy"] == "login"


@pytest.mark.asyncio
async def test_brute_force_against_one_account_is_throttled(client, small_login_budget):
    """Wrong passwords against a single account stop after the budget."""
    await client.post(
        "/api/v1/auth/register",
        json={
            "email": "target@example.com",
            "full_name": "Target",
            "password": "password123",
        },
    )
    limiter.reset()

    guesses = [
        {"email": "target@example.com", "password": f"guess-{i}"} for i in range(3)
    ]
    for guess in guesses:
        assert (await client.post("/api/v1/auth/login", json=guess)).status_code == 401

    blocked = await client.post(
        "/api/v1/auth/login",
        json={"email": "target@example.com", "password": "guess-99"},
    )
    assert blocked.status_code == 429


@pytest.mark.asyncio
async def test_login_budget_is_per_account(client, registered_user, small_login_budget):
    """Exhausting one account's budget must not lock out a different one."""
    payload = {"email": registered_user["email"], "password": "password123"}
    for _ in range(4):
        await client.post("/api/v1/auth/login", json=payload)

    assert (await client.post("/api/v1/auth/login", json=payload)).status_code == 429

    await client.post(
        "/api/v1/auth/register",
        json={"email": "bystander@example.com", "full_name": "B", "password": "password123"},
    )
    limiter.reset()
    other = await client.post(
        "/api/v1/auth/login",
        json={"email": "bystander@example.com", "password": "password123"},
    )
    assert other.status_code == 200


@pytest.mark.asyncio
async def test_registration_is_throttled(client, monkeypatch):
    monkeypatch.setattr(REGISTER_POLICY, "limit", 2)
    limiter.reset()

    for i in range(2):
        res = await client.post(
            "/api/v1/auth/register",
            json={
                "email": f"farming-{i}@example.com",
                "full_name": "Bot",
                "password": "password123",
            },
        )
        assert res.status_code == 201

    blocked = await client.post(
        "/api/v1/auth/register",
        json={"email": "farming-2@example.com", "full_name": "Bot", "password": "password123"},
    )
    assert blocked.status_code == 429


@pytest.mark.asyncio
async def test_rag_ask_is_throttled_per_user(
    client, registered_user, subject, seeded_chunks, monkeypatch
):
    monkeypatch.setattr(GENERATIVE_POLICY, "limit", 2)
    limiter.reset()

    await seeded_chunks(["A vector index accelerates nearest-neighbour search."])
    payload = {"subject_id": subject, "question": "What is a vector index?"}
    for _ in range(2):
        res = await client.post("/api/v1/rag/ask", json=payload, headers=registered_user["headers"])
        assert res.status_code == 200, res.text

    blocked = await client.post(
        "/api/v1/rag/ask", json=payload, headers=registered_user["headers"]
    )
    assert blocked.status_code == 429
    assert blocked.headers["X-RateLimit-Policy"] == "generative"


@pytest.mark.asyncio
async def test_generative_budget_is_per_user_not_shared(
    client, registered_user, other_user, subject, seeded_chunks, monkeypatch
):
    """One student's spend must not consume another's."""
    monkeypatch.setattr(GENERATIVE_POLICY, "limit", 2)
    limiter.reset()

    await seeded_chunks(["Embeddings map text into a numeric vector space."])
    payload = {"subject_id": subject, "question": "Explain embeddings."}
    for _ in range(3):
        await client.post("/api/v1/rag/ask", json=payload, headers=registered_user["headers"])

    mine = await client.post(
        "/api/v1/rag/ask", json=payload, headers=registered_user["headers"]
    )
    assert mine.status_code == 429

    # The other user never spent anything, so their first call still works.
    theirs = await client.post(
        "/api/v1/rag/ask", json=payload, headers=other_user["headers"]
    )
    assert theirs.status_code in (200, 404), theirs.text


@pytest.mark.asyncio
async def test_quiz_generation_is_throttled(
    client, registered_user, subject, seeded_chunks, monkeypatch
):
    monkeypatch.setattr(GENERATIVE_POLICY, "limit", 1)
    limiter.reset()

    await seeded_chunks(["Retrieval practice beats rereading for long-term memory."])
    payload = {"subject_id": subject, "num_questions": 2}

    first = await client.post(
        "/api/v1/quizzes/generate", json=payload, headers=registered_user["headers"]
    )
    assert first.status_code == 201, first.text

    blocked = await client.post(
        "/api/v1/quizzes/generate", json=payload, headers=registered_user["headers"]
    )
    assert blocked.status_code == 429


@pytest.mark.asyncio
async def test_flashcard_generation_is_throttled(
    client, registered_user, subject, seeded_chunks, monkeypatch
):
    monkeypatch.setattr(GENERATIVE_POLICY, "limit", 1)
    limiter.reset()

    await seeded_chunks(["Active recall strengthens long-term retention."])
    payload = {"subject_id": subject, "num_cards": 3}

    first = await client.post(
        "/api/v1/flashcards/generate", json=payload, headers=registered_user["headers"]
    )
    assert first.status_code == 201, first.text

    blocked = await client.post(
        "/api/v1/flashcards/generate", json=payload, headers=registered_user["headers"]
    )
    assert blocked.status_code == 429


@pytest.mark.asyncio
async def test_study_plan_generation_is_throttled(
    client, registered_user, subject, monkeypatch
):
    monkeypatch.setattr(GENERATIVE_POLICY, "limit", 1)
    limiter.reset()

    payload = {"subject_id": subject}

    first = await client.post(
        "/api/v1/study-plans/generate", json=payload, headers=registered_user["headers"]
    )
    assert first.status_code == 201, first.text

    blocked = await client.post(
        "/api/v1/study-plans/generate", json=payload, headers=registered_user["headers"]
    )
    assert blocked.status_code == 429


@pytest.mark.asyncio
async def test_document_upload_is_throttled(client, registered_user, subject, monkeypatch):
    monkeypatch.setattr(UPLOAD_POLICY, "limit", 1)
    limiter.reset()

    pdf = b"%PDF-1.4\n" + b"x" * 64
    first = await client.post(
        "/api/v1/documents/upload",
        data={"subject_id": subject},
        files={"file": ("notes.pdf", pdf, "application/pdf")},
        headers=registered_user["headers"],
    )
    assert first.status_code == 201, first.text

    blocked = await client.post(
        "/api/v1/documents/upload",
        data={"subject_id": subject},
        files={"file": ("more.pdf", pdf, "application/pdf")},
        headers=registered_user["headers"],
    )
    assert blocked.status_code == 429
    assert blocked.headers["X-RateLimit-Policy"] == "upload"


@pytest.mark.asyncio
async def test_limiter_can_be_disabled_for_local_development(
    client, registered_user, subject, seeded_chunks, monkeypatch
):
    """``RATE_LIMIT_ENABLED=False`` must be a real escape hatch, per .env.example."""
    monkeypatch.setattr(GENERATIVE_POLICY, "limit", 1)
    monkeypatch.setattr(settings, "RATE_LIMIT_ENABLED", False)
    limiter.reset()

    await seeded_chunks(["Disabled limiting still answers the question."])
    payload = {"subject_id": subject, "question": "Does this get blocked?"}
    for _ in range(3):
        res = await client.post(
            "/api/v1/rag/ask", json=payload, headers=registered_user["headers"]
        )
        assert res.status_code == 200, res.text


@pytest.mark.asyncio
async def test_throttling_never_replaces_a_401_on_an_unauthenticated_route(client):
    """A limit hit must not be confused with an auth failure, or vice versa."""
    assert (await client.post("/api/v1/rag/ask", json={"question": "hi"})).status_code == 401


def test_policy_reads_are_side_effect_free():
    """limit_size/window_seconds are what the dependency actually uses."""
    policy = RateLimitPolicy("probe", 7, 11, key_by_user=True)
    assert policy.limit_size() == 7
    assert policy.window_seconds() == 11
    assert policy.name == "probe"


# ---------------------------------------------------------------------------
# B2: per-policy isolation and honest scope
# ---------------------------------------------------------------------------


def test_a_short_window_policy_cannot_expire_a_long_window_policy():
    """The regression that motivated B2.

    All policies share one dict, so the original implementation swept every
    bucket with ``now - <the policy just touched>`` and deleted the ones it
    judged expired. Login and generative use a 60s window while upload uses
    3600s, so a single login request deleted the upload bucket outright and
    handed the caller a fresh hourly budget.

    Upload's own limit is reached first here, then unrelated 60s traffic happens.
    The next upload must still be refused.
    """
    store = SlidingWindowLimiter()
    hour = 3600.0

    # Exhaust the upload budget: 2 allowed, then blocked.
    assert store.hit("upload:1.2.3.4", limit=2, window=hour, now=0.0) is True
    assert store.hit("upload:1.2.3.4", limit=2, window=hour, now=0.0) is True
    assert store.hit("upload:1.2.3.4", limit=2, window=hour, now=0.0) is False

    # Unrelated 60s traffic, including enough elapsed time that a shared-window
    # sweep would have considered the upload bucket long expired.
    for _ in range(50):
        store.hit("login:9.9.9.9", limit=100, window=60.0, now=0.0)
    store.hit("generative:9.9.9.9", limit=100, window=60.0, now=120.0)

    # The upload budget must be untouched by all of that.
    assert store.hit("upload:1.2.3.4", limit=2, window=hour, now=0.0) is False


def test_a_long_window_policy_still_expires_on_its_own_schedule():
    """Isolation must not become permanence: an upload bucket still ages out."""
    store = SlidingWindowLimiter()
    hour = 3600.0

    assert store.hit("upload:1.2.3.4", limit=1, window=hour, now=0.0) is True
    assert store.hit("upload:1.2.3.4", limit=1, window=hour, now=0.0) is False
    # Well past the hour: the budget is genuinely restored.
    assert store.hit("upload:1.2.3.4", limit=1, window=hour, now=hour + 1.0) is True


def test_retry_after_uses_the_buckets_own_window():
    """A 60s policy must not report a 3600s wait (or vice versa)."""
    store = SlidingWindowLimiter()
    store.hit("upload:1.2.3.4", limit=5, window=3600.0, now=0.0)

    # The bucket knows it is hourly, so the answer tracks the hour rather than
    # whatever window the caller happened to pass.
    assert store.retry_after("upload:1.2.3.4", 3600.0, now=0.0) == 3600 + 1
    assert store.retry_after("upload:1.2.3.4", 60.0, now=0.0) == 3600 + 1

    store.reset()
    store.hit("login:9.9.9.9", limit=5, window=60.0, now=0.0)
    assert store.retry_after("login:9.9.9.9", 3600.0, now=0.0) == 60 + 1


def test_spent_buckets_are_reclaimed_once_the_key_cap_is_reached():
    """Idle keys must not accumulate once eviction is triggered."""
    store = SlidingWindowLimiter(max_keys=4)
    for i in range(4):
        store.hit(f"stale-{i}", limit=1, window=60.0, now=0.0)

    assert store.tracked_key_count() == 4
    # A request at t=200 pushes the cap over, so pruning runs and every one of
    # those buckets is past its own 60s window.
    store.hit("fresh", limit=1, window=60.0, now=200.0)

    assert store.tracked_key_count() == 1
    assert store.tracked_key_count() <= 4


def test_tracked_key_count_matches_the_dict():
    store = SlidingWindowLimiter()
    assert store.tracked_key_count() == 0
    store.hit("a", limit=2, window=60.0, now=0.0)
    store.hit("a", limit=2, window=60.0, now=0.0)
    assert store.tracked_key_count() == 1


def test_key_cap_still_bounds_memory():
    """The bound survives the move to per-bucket windows."""
    store = SlidingWindowLimiter(max_keys=10)
    for i in range(500):
        store.hit(f"rotating-{i}", limit=5, window=3600.0, now=0.0)

    assert store.tracked_key_count() <= 10


def test_blocked_requests_do_not_extend_the_window():
    """A rejected caller must not be able to lock itself out indefinitely."""
    store = SlidingWindowLimiter()
    store.hit("k", limit=1, window=60.0, now=0.0)

    # Hammer it while blocked. If rejections were recorded, the original hit
    # would be pushed out and the budget would never come back.
    for t in range(60):
        assert store.hit("k", limit=1, window=60.0, now=float(t)) is False

    assert store.hit("k", limit=1, window=60.0, now=61.0) is True


@pytest.mark.asyncio
async def test_policies_cannot_spend_each_other_budgets_through_the_dependency(
    client, registered_user, subject, seeded_chunks, monkeypatch
):
    """End-to-end: exhausting login must not reset the upload budget.

    Uses the real bucket naming (``f"{policy.name}:{key}"``) via two real
    endpoints, so it would catch a regression in the dependency wiring as well
    as in the store.
    """
    monkeypatch.setattr(LOGIN_POLICY, "limit", 2)
    monkeypatch.setattr(UPLOAD_POLICY, "limit", 2)
    limiter.reset()

    import io

    try:
        # Exhaust the login budget. Login is keyed by address+email, so these
        # land in a completely different bucket from the upload one, which is
        # keyed by user id.
        for _ in range(2):
            res = await client.post(
                "/api/v1/auth/login",
                json={"email": "nobody@example.com", "password": "x"},
            )
            assert res.status_code == 401, res.text

        # Uploading is unaffected by the spent login budget.
        pdf = b"%PDF-1.4\n" + b"y" * 64
        files = {"file": ("one.pdf", io.BytesIO(pdf), "application/pdf")}
        for _ in range(2):
            res = await client.post(
                "/api/v1/documents/upload",
                data={"subject_id": subject},
                files=files,
                headers=registered_user["headers"],
            )
            assert res.status_code == 201, res.text

        # The upload budget is its own and is now spent.
        blocked = await client.post(
            "/api/v1/documents/upload",
            data={"subject_id": subject},
            files={"file": ("three.pdf", io.BytesIO(pdf), "application/pdf")},
            headers=registered_user["headers"],
        )
        assert blocked.status_code == 429
        assert blocked.headers["X-RateLimit-Policy"] == "upload"

        # The 2 x 60s upload traffic above must not have reset the login budget.
        assert (
            await client.post(
                "/api/v1/auth/login",
                json={"email": "nobody@example.com", "password": "x"},
            )
        ).status_code == 429
    finally:
        limiter.reset()


# ---------------------------------------------------------------------------
# B2: storage scope is explicit, not implied
# ---------------------------------------------------------------------------


def test_storage_backend_is_the_in_process_limiter():
    """The seam a shared store would be selected through."""
    backend = get_backend()
    assert backend is limiter
    assert isinstance(backend, RateLimitBackend)


def test_in_process_limiter_satisfies_the_backend_protocol():
    """A Redis implementation would have to satisfy this same interface."""
    assert isinstance(SlidingWindowLimiter(), RateLimitBackend)


def test_worker_count_defaults_to_one():
    """The documented deployment is a single worker, where the limits are exact."""
    assert type(settings).model_fields["RATE_LIMIT_WORKER_COUNT"].default == 1
    assert settings.RATE_LIMIT_WORKER_COUNT >= 1


def test_single_worker_scope_is_logged_as_informational(
    caplog, monkeypatch
):
    monkeypatch.setattr(settings, "RATE_LIMIT_WORKER_COUNT", 1)
    monkeypatch.setattr(settings, "RATE_LIMIT_ENABLED", True)

    with caplog.at_level("INFO", logger="app.rate_limit"):
        describe_effective_limit_scope()

    records = [r for r in caplog.records if r.message == "rate_limit_scope"]
    assert records, "the scope must be stated at startup, not left to the source"
    assert records[0].storage == "in-process"


def test_multi_worker_scope_is_logged_as_a_warning(caplog, monkeypatch):
    """Above one worker the real per-caller budget is N x the configured one.

    That materially weakens the abuse control on login, register and the
    generative endpoints, so it has to be a warning an operator actually sees
    rather than a comment in a docstring.
    """
    monkeypatch.setattr(settings, "RATE_LIMIT_WORKER_COUNT", 4)
    monkeypatch.setattr(settings, "RATE_LIMIT_ENABLED", True)

    with caplog.at_level("WARNING", logger="app.rate_limit"):
        describe_effective_limit_scope()

    records = [
        r for r in caplog.records if r.message == "rate_limit_scope_is_per_process"
    ]
    assert records, "a multi-worker deployment must be warned about"
    record = records[0]
    assert record.declared_worker_count == 4
    assert record.login_effective_attempts == LOGIN_POLICY.limit * 4
    assert record.upload_effective_attempts == UPLOAD_POLICY.limit * 4
    assert record.generative_effective_attempts == GENERATIVE_POLICY.limit * 4


def test_disabled_limiting_does_not_warn_about_worker_scope(caplog, monkeypatch):
    """With limiting off there is no weakened limit to report."""
    monkeypatch.setattr(settings, "RATE_LIMIT_WORKER_COUNT", 8)
    monkeypatch.setattr(settings, "RATE_LIMIT_ENABLED", False)

    with caplog.at_level("WARNING", logger="app.rate_limit"):
        describe_effective_limit_scope()

    assert not [
        r for r in caplog.records if r.message == "rate_limit_scope_is_per_process"
    ]


def test_rate_limiting_is_still_installed_on_the_expensive_routes():
    """B2 is a hardening change; it must not have removed any enforcement."""
    source = Path(__file__).resolve().parents[1] / "app" / "api" / "v1"

    login = (source / "auth.py").read_text(encoding="utf-8")
    assert "limit_login" in login
    assert "limit_register" in login

    for name, marker in (
        ("rag.py", "limit_generative"),
        ("quizzes.py", "limit_generative"),
        ("flashcards.py", "limit_generative"),
        ("documents.py", "limit_upload"),
    ):
        assert marker in (source / name).read_text(encoding="utf-8"), name


# ---------------------------------------------------------------------------
# A4: a user-scoped policy is keyed by the resolved user, not by who ran first
# ---------------------------------------------------------------------------
#
# The limiter used to read ``request.state.current_user_id``, which
# ``get_current_user`` wrote as a side effect. That is correct only if the auth
# dependency happens to run first, and sibling route parameters are not a
# guarantee of that -- FastAPI gives no ordering contract between them. When the
# order went the other way the limiter fell back to the client address, so
# every account behind one NAT pooled into a single generative budget and
# throttled each other.
#
# The fix declares the auth resolver as a *sub-dependency of the limiter*, so
# FastAPI's solver establishes the order and caches the resolver. The tests
# below pin that: the signature, the wiring on every affected route, and the
# behaviour when the limiter is deliberately declared before auth.

_USER_A = "11111111-1111-1111-1111-111111111111"
_USER_B = "22222222-2222-2222-2222-222222222222"


def _user_from_header(x_test_user: str = Header(...)) -> SimpleNamespace:
    """Stand-in for ``get_current_user``: a fake user, no JWT or database."""
    return SimpleNamespace(id=uuid.UUID(x_test_user))


def _app_with_limiter_declared_first(policy: RateLimitPolicy) -> FastAPI:
    """A route listing the limiter *before* the auth dependency.

    The real routers happen to list auth first. This is the shape they do not
    have and the one the old ``request.state`` read got wrong: with the limiter
    resolved first there is no authenticated id on the state object yet, so it
    fell back to the address and a per-user budget became a per-address one.
    """
    limited = rate_limit(policy, user_dependency=_user_from_header)
    app = FastAPI()

    @app.get("/probe")
    async def probe(
        _limited: None = Depends(limited),
        user: SimpleNamespace = Depends(_user_from_header),
    ):
        return {"user_id": str(user.id)}

    return app


def test_a_user_scoped_policy_must_be_told_how_to_resolve_the_user():
    """No resolver means no user-scoped policy -- it is not allowed to guess.

    The old signature made the resolver optional and fell back to the address,
    so a route that forgot to pass one still throttled: wrongly, silently, and
    only visibly once real traffic shared an address. Requiring it turns that
    into an error at import, before any request is served.
    """
    with pytest.raises(ValueError, match="user-scoped"):
        rate_limit(GENERATIVE_POLICY)

    with pytest.raises(ValueError, match="user-scoped"):
        rate_limit(UPLOAD_POLICY)


def test_a_user_scoped_policy_rejects_a_second_key_source():
    """``identity`` and the user id are two different keys for one request.

    Resolved by refusing the combination rather than by picking a winner, so
    the key a caller is throttled against is never decided by argument order.
    """
    with pytest.raises(ValueError, match="conflicting"):
        rate_limit(GENERATIVE_POLICY, identity=email_identity, user_dependency=get_current_user)


def test_the_user_scoped_dependency_declares_auth_as_a_sub_dependency():
    """The ordering guarantee lives in the signature, not in route order.

    This is the assertion the previous implementation could not pass: reading
    ``request.state`` leaves no sub-dependency to find here.
    """
    dependency = rate_limit(GENERATIVE_POLICY, user_dependency=get_current_user)

    parameters = inspect.signature(dependency).parameters
    assert "authenticated_user" in parameters, (
        "the limiter must receive the user as a declared sub-dependency"
    )
    marker = parameters["authenticated_user"].default
    assert isinstance(marker, DependsMarker)
    assert marker.dependency is get_current_user


def test_the_unauthenticated_policies_declare_no_auth_dependency():
    """Login and registration must stay reachable without a token.

    They are ``key_by_user=False``, so they take the branch that takes only a
    request and cannot demand a ``User``. This is the structural reason their
    behaviour is unchanged, rather than a policy that happens to be correct.
    """
    for policy, identity in ((LOGIN_POLICY, email_identity), (REGISTER_POLICY, None)):
        dependency = rate_limit(policy, identity=identity)
        assert list(inspect.signature(dependency).parameters) == ["request"]


def test_a_limiter_declared_before_auth_still_keys_on_the_user():
    """The order-independence case, through real requests.

    Both users arrive from the same client address, so a limiter that had
    fallen back to the address would share one bucket and block the second
    user. It does not: the fallback is gone, not merely unused here.
    """
    policy = RateLimitPolicy("a4_probe", limit=2, window=60, key_by_user=True)
    limiter.reset()

    try:
        with TestClient(_app_with_limiter_declared_first(policy)) as client:
            mine = {"X-Test-User": _USER_A}
            theirs = {"X-Test-User": _USER_B}

            assert client.get("/probe", headers=mine).status_code == 200
            assert client.get("/probe", headers=mine).status_code == 200
            assert client.get("/probe", headers=mine).status_code == 429

            # A different user from the same address has an untouched budget.
            assert client.get("/probe", headers=theirs).status_code == 200
            assert client.get("/probe", headers=theirs).status_code == 200
            assert client.get("/probe", headers=theirs).status_code == 429
    finally:
        limiter.reset()


def test_the_limiter_keys_on_the_user_id_and_never_on_the_address():
    """The bucket is literally named after the user, not the address.

    Asserting the stored key rather than only the status code: a 429 for the
    right reason is the observable, and the key is what makes the per-user
    budget per-user.
    """
    policy = RateLimitPolicy("a4_key", limit=1, window=60, key_by_user=True)
    limiter.reset()

    try:
        with TestClient(_app_with_limiter_declared_first(policy)) as client:
            client.get("/probe", headers={"X-Test-User": _USER_A})

        tracked = list(limiter._hits)
        assert tracked == [f"a4_key:{_USER_A}"], tracked
    finally:
        limiter.reset()


def test_the_user_resolver_runs_once_per_request_not_once_per_dependent():
    """Declaring auth twice must not decode the token or load the user twice.

    The route and the limiter both need the user, which is the whole reason the
    original code reached for ``request.state``. FastAPI caches by dependency
    callable, so declaring the same resolver in both places resolves it once.
    That is why the sub-dependency costs nothing extra and the state handoff
    was never necessary.
    """
    calls = []
    resolver = _user_from_header

    def counting_resolver(x_test_user: str = Header(...)) -> SimpleNamespace:
        calls.append(x_test_user)
        return resolver(x_test_user=x_test_user)

    limited = rate_limit(
        RateLimitPolicy("a4_once", 5, 60, key_by_user=True), user_dependency=counting_resolver
    )
    app = FastAPI()

    @app.get("/once")
    async def once(
        _limited: None = Depends(limited),
        user: SimpleNamespace = Depends(counting_resolver),
    ):
        return {"user_id": str(user.id)}

    limiter.reset()
    try:
        with TestClient(app) as client:
            res = client.get("/once", headers={"X-Test-User": _USER_A})

        assert res.status_code == 200, res.text
        assert calls == [_USER_A], f"auth resolved {len(calls)} times for one request"
    finally:
        limiter.reset()


@pytest.mark.asyncio
async def test_a_spent_user_does_not_lock_out_another_on_a_real_route(
    client, registered_user, other_user, subject, seeded_chunks, monkeypatch
):
    """The reported symptom, on a real route with real tokens.

    The shared test client presents one address for every request, so any
    coupling between these two users is necessarily about the key.
    """
    monkeypatch.setattr(GENERATIVE_POLICY, "limit", 2)
    limiter.reset()

    try:
        await seeded_chunks(["Retrieval practice beats rereading for long-term memory."])
        payload = {"subject_id": subject, "question": "What is retrieval practice?"}

        for _ in range(2):
            res = await client.post(
                "/api/v1/rag/ask", json=payload, headers=registered_user["headers"]
            )
            assert res.status_code == 200, res.text

        assert (
            await client.post("/api/v1/rag/ask", json=payload, headers=registered_user["headers"])
        ).status_code == 429

        # The other student never spent anything, so their first call still works.
        theirs = await client.post(
            "/api/v1/rag/ask", json=payload, headers=other_user["headers"]
        )
        assert theirs.status_code in (200, 404), theirs.text
    finally:
        limiter.reset()


@pytest.mark.asyncio
async def test_the_real_route_keys_its_bucket_on_the_authenticated_user_id(
    client, registered_user, subject, seeded_chunks
):
    """Same guarantee as the probe, read off the real dependency wiring.

    Complements the behavioural test above: that one shows the effect, this one
    shows the key, so a future change that re-introduced an address component
    while still passing a coincidence-based behaviour test would be caught.
    """
    limiter.reset()
    try:
        await seeded_chunks(["A vector index accelerates nearest-neighbour search."])
        await client.post(
            "/api/v1/rag/ask",
            json={"subject_id": subject, "question": "What is a vector index?"},
            headers=registered_user["headers"],
        )

        tracked = list(limiter._hits)
        assert f"generative:{registered_user['user_id']}" in tracked, tracked
        assert not any("test" in key for key in tracked), (
            f"the address leaked into a user-scoped key: {tracked}"
        )
    finally:
        limiter.reset()


def test_every_user_scoped_route_declares_the_resolver():
    """Each affected router opts in explicitly, so none can drift back.

    Checked in the source because the alternative -- walking FastAPI's assembled
    dependency tree -- depends on private router internals that changed shape in
    0.141 (``_IncludedRouter`` no longer flattens into ``app.routes``). The
    behavioural tests above cover what this wiring actually produces.
    """
    source = Path(__file__).resolve().parents[1] / "app" / "api" / "v1"

    for name in ("documents", "flashcards", "quizzes", "rag", "study_plans"):
        text = (source / f"{name}.py").read_text(encoding="utf-8")
        assert "user_dependency=get_current_user" in text, name


def test_the_unauthenticated_routes_do_not_ask_for_a_user():
    """Guards requirement 3 from the other side.

    Login and registration must not grow an auth sub-dependency: doing so would
    make them reject anonymous callers, which is the exact regression the
    key_by_user split exists to prevent.
    """
    login = rate_limit(LOGIN_POLICY, identity=email_identity)
    register = rate_limit(REGISTER_POLICY)

    for dependency in (login, register):
        parameters = inspect.signature(dependency).parameters
        assert list(parameters) == ["request"]
