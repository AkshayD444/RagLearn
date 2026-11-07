import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { api } from "@/lib/api";
import {
  makeAnalyticsOverview,
  makeDocumentStatus,
  makeFlashcardDeck,
  makeFlashcardDeckSummary,
  makeFlashcardReviewSummary,
  makeFlashcardTopicProgress,
  makePage,
  makeQuizAttemptDetail,
  makeQuizAttemptSummary,
  makeQuizSummary,
  makeSubject,
} from "@/test/factories";

/**
 * These tests exercise the real `ApiClient` against a stubbed `fetch`, so they
 * cover the shared request plumbing: base URL, auth header, content type,
 * error unwrapping, and the 204 short-circuit.
 */

const BASE = "http://localhost:8000/api/v1";

function jsonResponse(body: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  } as unknown as Response;
}

function emptyResponse(status: number): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => {
      throw new Error("no body");
    },
  } as unknown as Response;
}

let fetchMock: ReturnType<typeof vi.fn>;

beforeEach(() => {
  fetchMock = vi.fn();
  vi.stubGlobal("fetch", fetchMock);
  window.localStorage.clear();
  document.cookie.split(";").forEach((c) => {
    const name = c.split("=")[0].trim();
    if (name) document.cookie = `${name}=; expires=Thu, 01 Jan 1970 00:00:00 GMT; path=/`;
  });
});

afterEach(() => {
  vi.unstubAllGlobals();
});

function lastCall(): [string, RequestInit] {
  return fetchMock.mock.calls[fetchMock.mock.calls.length - 1] as [string, RequestInit];
}

function headersOf(call: RequestInit): Record<string, string> {
  return call.headers as Record<string, string>;
}

describe("api request plumbing", () => {
  it("prefixes every endpoint with the configured base URL", async () => {
    fetchMock.mockResolvedValue(jsonResponse([]));

    await api.getSubjects();

    expect(lastCall()[0]).toBe(`${BASE}/subjects`);
  });

  it("sends cookies with every request instead of an Authorization header", async () => {
    fetchMock.mockResolvedValue(jsonResponse([]));

    await api.getSubjects();

    const [, call] = lastCall();
    // The whole point of B2: the credential rides in a cookie the browser
    // attaches, so nothing in the page has to hold it.
    expect(call.credentials).toBe("include");
    expect(headersOf(call).Authorization).toBeUndefined();
  });

  it("never reads the session out of localStorage", async () => {
    window.localStorage.setItem("access_token", "leftover-from-an-old-build");
    fetchMock.mockResolvedValue(jsonResponse([]));

    await api.getSubjects();

    expect(headersOf(lastCall()[1]).Authorization).toBeUndefined();
    expect(window.localStorage.getItem("access_token")).toBe("leftover-from-an-old-build");
  });

  it("echoes the CSRF cookie into a header on writes", async () => {
    document.cookie = "csrf_token=csrf-value-123";
    fetchMock.mockResolvedValue(jsonResponse({ id: "1" }));

    await api.createSubject({ title: "Physics" });

    expect(headersOf(lastCall()[1])["X-CSRF-Token"]).toBe("csrf-value-123");
  });

  it("does not send a CSRF header on safe requests", async () => {
    document.cookie = "csrf_token=csrf-value-123";
    fetchMock.mockResolvedValue(jsonResponse([]));

    await api.getSubjects();

    expect(headersOf(lastCall()[1])["X-CSRF-Token"]).toBeUndefined();
  });

  it("surfaces the server's CSRF rejection rather than retrying it", async () => {
    fetchMock.mockResolvedValue(jsonResponse({ detail: "CSRF token missing or invalid." }, 403));

    await expect(api.createSubject({ title: "Physics" })).rejects.toThrow("CSRF token");
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("sets a JSON content type for plain-object bodies", async () => {
    fetchMock.mockResolvedValue(jsonResponse({ id: "1" }));

    await api.createSubject({ title: "Physics", description: "", color: "#fff" });

    const [, call] = lastCall();
    expect(call.method).toBe("POST");
    expect(headersOf(call)["Content-Type"]).toBe("application/json");
    expect(call.body).toBe(JSON.stringify({ title: "Physics", description: "", color: "#fff" }));
  });

  it("leaves the content type alone for FormData uploads", async () => {
    fetchMock.mockResolvedValue(jsonResponse({ id: "doc-1" }));
    const file = new File(["%PDF-1.4"], "lecture.pdf", { type: "application/pdf" });

    await api.uploadDocument("subject-1", file);

    const [url, call] = lastCall();
    expect(url).toBe(`${BASE}/documents/upload`);
    expect(headersOf(call)["Content-Type"]).toBeUndefined();
    const body = call.body as FormData;
    expect(body.get("subject_id")).toBe("subject-1");
    expect(body.get("file")).toBeInstanceOf(File);
  });

  it("throws the FastAPI `detail` string on an error response", async () => {
    fetchMock.mockResolvedValue(jsonResponse({ detail: "No study plan found." }, 404));

    await expect(api.getSubject("missing")).rejects.toThrow("No study plan found.");
  });

  it("falls back to a generic message when the error body has no detail", async () => {
    fetchMock.mockResolvedValue(jsonResponse({}, 500));

    await expect(api.getSubject("missing")).rejects.toThrow("HTTP Error 500");
  });

  it("falls back to a generic message when the error body is not JSON", async () => {
    fetchMock.mockResolvedValue({
      ok: false,
      status: 502,
      json: async () => {
        throw new SyntaxError("Unexpected token <");
      },
    } as unknown as Response);

    await expect(api.getSubject("missing")).rejects.toThrow("Request failed");
  });

  it("returns an empty object for 204 responses instead of parsing a body", async () => {
    fetchMock.mockResolvedValue(emptyResponse(204));

    await expect(api.deleteSubject("subject-1")).resolves.toEqual({});
    expect(lastCall()[1].method).toBe("DELETE");
  });
});

describe("api.getLatestStudyPlan", () => {
  it("requests the plan for the given subject", async () => {
    fetchMock.mockResolvedValue(jsonResponse({ id: "plan-1" }));

    await api.getLatestStudyPlan("subject-1");

    expect(lastCall()[0]).toBe(`${BASE}/study-plans/latest?subject_id=subject-1`);
  });

  it("URL-encodes the subject filter", async () => {
    fetchMock.mockResolvedValue(jsonResponse({ id: "plan-1" }));

    await api.getLatestStudyPlan("subject 1&x=2");

    expect(lastCall()[0]).toBe(`${BASE}/study-plans/latest?subject_id=subject%201%26x%3D2`);
  });

  it("omits the filter when no subject is given", async () => {
    fetchMock.mockResolvedValue(jsonResponse({ id: "plan-1" }));

    await api.getLatestStudyPlan();

    expect(lastCall()[0]).toBe(`${BASE}/study-plans/latest`);
  });

  it("maps the empty-plan 404 onto null", async () => {
    fetchMock.mockResolvedValue(jsonResponse({ detail: "No study plan found." }, 404));

    await expect(api.getLatestStudyPlan("subject-1")).resolves.toBeNull();
  });

  it("rethrows any other error instead of masking it as null", async () => {
    fetchMock.mockResolvedValue(jsonResponse({ detail: "Subject not found." }, 404));

    await expect(api.getLatestStudyPlan("subject-1")).rejects.toThrow("Subject not found.");
  });
});

describe("api request bodies", () => {
  it("sends a normalised quiz request with a null topic", async () => {
    fetchMock.mockResolvedValue(jsonResponse({ id: "quiz-1" }));

    await api.generateQuiz("subject-1", 5, "medium");

    const [url, call] = lastCall();
    expect(url).toBe(`${BASE}/quizzes/generate`);
    expect(JSON.parse(call.body as string)).toEqual({
      subject_id: "subject-1",
      question_count: 5,
      difficulty: "medium",
      topic: null,
    });
  });

  it("sends a trimmed topic when one is provided", async () => {
    fetchMock.mockResolvedValue(jsonResponse({ id: "quiz-1" }));

    await api.generateQuiz("subject-1", 3, "hard", "Photosynthesis");

    expect(JSON.parse(lastCall()[1].body as string).topic).toBe("Photosynthesis");
  });

  it("includes started_at only when a start timestamp was captured", async () => {
    fetchMock.mockResolvedValue(jsonResponse({ quiz_id: "quiz-1" }));

    await api.submitQuiz("quiz-1", [{ question_id: "q1", selected_option: "A" }]);
    expect(JSON.parse(lastCall()[1].body as string)).toEqual({
      answers: [{ question_id: "q1", selected_option: "A" }],
    });

    await api.submitQuiz(
      "quiz-1",
      [{ question_id: "q1", selected_option: "A" }],
      { startedAt: "2026-01-01T00:00:00.000Z" },
    );
    expect(JSON.parse(lastCall()[1].body as string).started_at).toBe("2026-01-01T00:00:00.000Z");
  });

  it("appends the limit query parameter for recommendation endpoints", async () => {
    fetchMock.mockResolvedValue(jsonResponse([]));

    await api.getTopRecommendations();
    expect(lastCall()[0]).toBe(`${BASE}/recommendations/top?limit=5`);

    await api.getSubjectRecommendations("subject-1", 3);
    expect(lastCall()[0]).toBe(`${BASE}/recommendations/subjects/subject-1?limit=3`);
  });

  it("PATCHes study-plan completion with the requested flag", async () => {
    fetchMock.mockResolvedValue(jsonResponse({ id: "plan-1" }));

    await api.completeStudyPlanItem("item-1", false);

    const [url, call] = lastCall();
    expect(url).toBe(`${BASE}/study-plans/items/item-1/complete`);
    expect(call.method).toBe("PATCH");
    expect(JSON.parse(call.body as string)).toEqual({ completed: false });
  });

  it("defaults study-plan completion to true", async () => {
    fetchMock.mockResolvedValue(jsonResponse({ id: "plan-1" }));

    await api.completeStudyPlanItem("item-1");

    expect(JSON.parse(lastCall()[1].body as string)).toEqual({ completed: true });
  });

  it("posts the study-plan generation options verbatim", async () => {
    fetchMock.mockResolvedValue(jsonResponse({ id: "plan-1" }));

    await api.generateStudyPlan({
      subject_id: "subject-1",
      target_date: "2026-01-17",
      available_minutes_per_day: 30,
      requested_sessions: null,
      max_topics_per_session: 3,
    });

    expect(JSON.parse(lastCall()[1].body as string)).toEqual({
      subject_id: "subject-1",
      target_date: "2026-01-17",
      available_minutes_per_day: 30,
      requested_sessions: null,
      max_topics_per_session: 3,
    });
  });

  it("sends the RAG ask payload with the default top_k", async () => {
    fetchMock.mockResolvedValue(jsonResponse({ answer: "ok" }));

    await api.askRagTutor("subject-1", "What is SN2?");

    const [url, call] = lastCall();
    expect(url).toBe(`${BASE}/rag/ask`);
    expect(JSON.parse(call.body as string)).toEqual({
      subject_id: "subject-1",
      question: "What is SN2?",
      top_k: 5,
    });
  });
});

describe("api student intelligence endpoints", () => {
  it("requests subject performance", async () => {
    fetchMock.mockResolvedValue(jsonResponse({ subject_id: "subject-1" }));

    await api.getSubjectPerformance("subject-1");

    expect(lastCall()[0]).toBe(`${BASE}/analytics/subjects/subject-1/performance`);
  });

  it("requests topic-level mastery", async () => {
    fetchMock.mockResolvedValue(jsonResponse([]));

    await api.getSubjectMastery("subject-1");

    expect(lastCall()[0]).toBe(`${BASE}/analytics/subjects/subject-1/mastery`);
  });

  it("requests weak topics with the default limit", async () => {
    fetchMock.mockResolvedValue(jsonResponse([]));

    await api.getSubjectWeakTopics("subject-1");

    expect(lastCall()[0]).toBe(
      `${BASE}/analytics/subjects/subject-1/weak-topics?limit=20`,
    );
  });

  it("passes an explicit weak-topic limit through", async () => {
    fetchMock.mockResolvedValue(jsonResponse([]));

    await api.getSubjectWeakTopics("subject-1", 5);

    expect(lastCall()[0]).toBe(
      `${BASE}/analytics/subjects/subject-1/weak-topics?limit=5`,
    );
  });
});

describe("api flashcard endpoints", () => {
  it("generates a deck for a subject", async () => {
    const deck = makeFlashcardDeck();
    fetchMock.mockResolvedValue(jsonResponse(deck));

    const result = await api.generateFlashcards("subject-1", 6, "Vector Databases");

    expect(result).toEqual(deck);
    const [url, call] = lastCall();
    expect(url).toBe(`${BASE}/flashcards/generate`);
    expect(call.method).toBe("POST");
    expect(JSON.parse(call.body as string)).toEqual({
      subject_id: "subject-1",
      card_count: 6,
      topic: "Vector Databases",
    });
  });

  it("normalizes a missing topic to null when generating", async () => {
    fetchMock.mockResolvedValue(jsonResponse(makeFlashcardDeck()));

    await api.generateFlashcards("subject-1", 3);

    expect(JSON.parse(lastCall()[1].body as string).topic).toBeNull();
  });

  it("lists decks for a subject", async () => {
    const decks = [makeFlashcardDeckSummary()];
    fetchMock.mockResolvedValue(jsonResponse(decks));

    const result = await api.getFlashcardDecks("subject-1");

    expect(result).toEqual(decks);
    expect(lastCall()[0]).toBe(`${BASE}/flashcards/decks?subject_id=subject-1`);
  });

  it("fetches a single deck with its cards", async () => {
    const deck = makeFlashcardDeck();
    fetchMock.mockResolvedValue(jsonResponse(deck));

    const result = await api.getFlashcardDeck("deck-1");

    expect(result.cards).toHaveLength(2);
    expect(result.cards[0].source_document_title).toBe("lecture-01.pdf");
    expect(lastCall()[0]).toBe(`${BASE}/flashcards/decks/deck-1`);
  });

  it("deletes a deck and tolerates the 204 empty body", async () => {
    fetchMock.mockResolvedValue(emptyResponse(204));

    await expect(api.deleteFlashcardDeck("deck-1")).resolves.toEqual({});

    const [url, call] = lastCall();
    expect(url).toBe(`${BASE}/flashcards/decks/deck-1`);
    expect(call.method).toBe("DELETE");
  });

  it("submits self-graded reviews for a deck", async () => {
    const summary = makeFlashcardReviewSummary();
    fetchMock.mockResolvedValue(jsonResponse(summary));

    const result = await api.reviewFlashcardDeck("deck-1", [
      { card_id: "card-1", is_correct: true, response: "  embeddings  " },
    ]);

    expect(result.percentage).toBe(100);
    const [url, call] = lastCall();
    expect(url).toBe(`${BASE}/flashcards/decks/deck-1/review`);
    expect(call.method).toBe("POST");
    expect(JSON.parse(call.body as string)).toEqual({
      reviews: [{ card_id: "card-1", is_correct: true, response: "  embeddings  " }],
    });
  });

  it("requests per-topic flashcard progress", async () => {
    const progress = [makeFlashcardTopicProgress()];
    fetchMock.mockResolvedValue(jsonResponse(progress));

    const result = await api.getFlashcardProgress("subject-1");

    expect(result[0].mastery_level).toBe("developing");
    expect(lastCall()[0]).toBe(`${BASE}/flashcards/subjects/subject-1/progress`);
  });

  it("surfaces the backend detail message for a rejected generation", async () => {
    fetchMock.mockResolvedValue(
      jsonResponse({ detail: "No processed documents found for this subject." }, 400),
    );

    await expect(api.generateFlashcards("subject-1", 5)).rejects.toThrow(
      "No processed documents found for this subject.",
    );
  });
});

describe("api: subject updates", () => {
  it("patches only the fields the caller changed", async () => {
    fetchMock.mockResolvedValue(jsonResponse(makeSubject({ title: "Renamed" })));

    const result = await api.updateSubject("subject-1", { title: "Renamed" });

    expect(result.title).toBe("Renamed");
    const [url, call] = lastCall();
    expect(url).toBe(`${BASE}/subjects/subject-1`);
    expect(call.method).toBe("PATCH");
    expect(JSON.parse(call.body as string)).toEqual({ title: "Renamed" });
  });

  it("omits a field that was left undefined", async () => {
    fetchMock.mockResolvedValue(jsonResponse(makeSubject()));

    await api.updateSubject("subject-1", { title: "Kept", description: undefined });

    expect(JSON.parse(lastCall()[1].body as string)).toEqual({ title: "Kept" });
  });

  it("propagates a validation failure", async () => {
    fetchMock.mockResolvedValue(jsonResponse({ detail: "Title must not be blank." }, 422));

    await expect(api.updateSubject("subject-1", { title: "   " })).rejects.toThrow(
      "Title must not be blank.",
    );
  });
});

describe("api: document status and reprocessing", () => {
  it("reads the compact polling status", async () => {
    fetchMock.mockResolvedValue(jsonResponse(makeDocumentStatus({ chunk_count: 12 })));

    const result = await api.getDocumentStatus("doc-1");

    expect(result.chunk_count).toBe(12);
    const [url, call] = lastCall();
    expect(url).toBe(`${BASE}/documents/doc-1/status`);
    // Left as the fetch default rather than a bodyless explicit POST.
    expect(call.method).toBeUndefined();
  });

  it("posts the reprocess request and returns the new status", async () => {
    fetchMock.mockResolvedValue(
      jsonResponse(makeDocumentStatus({ status: "PROCESSING" })),
    );

    const result = await api.reprocessDocument("doc-1");

    expect(result.status).toBe("PROCESSING");
    const [url, call] = lastCall();
    expect(url).toBe(`${BASE}/documents/doc-1/reprocess`);
    expect(call.method).toBe("POST");
  });

  it("surfaces the 409 raised while a document is still processing", async () => {
    fetchMock.mockResolvedValue(
      jsonResponse({ detail: "Document is still being processed." }, 409),
    );

    await expect(api.reprocessDocument("doc-1")).rejects.toThrow(
      "Document is still being processed.",
    );
  });
});

describe("api: bounded history and analytics", () => {
  it("passes an explicit limit and offset through as query parameters", async () => {
    fetchMock.mockResolvedValue(jsonResponse(makePage([makeQuizSummary()])));

    await api.getQuizzes({ subjectId: "subject-1", limit: 20, offset: 40 });

    const url = new URL(lastCall()[0]);
    expect(url.pathname).toBe("/api/v1/quizzes");
    expect(url.searchParams.get("subject_id")).toBe("subject-1");
    expect(url.searchParams.get("limit")).toBe("20");
    expect(url.searchParams.get("offset")).toBe("40");
  });

  it("omits the subject filter when the list is not scoped", async () => {
    fetchMock.mockResolvedValue(jsonResponse(makePage([])));

    await api.getQuizzes();

    const url = new URL(lastCall()[0]);
    expect(url.searchParams.has("subject_id")).toBe(false);
    expect(url.searchParams.has("limit")).toBe(false);
    expect(url.searchParams.has("offset")).toBe(false);
  });

  it("exposes has_more so the UI can bound its own paging", async () => {
    fetchMock.mockResolvedValue(
      jsonResponse(makePage([makeQuizSummary()], { total: 40, limit: 20, offset: 0 })),
    );

    const page = await api.getQuizzes({ limit: 20, offset: 0 });

    expect(page.has_more).toBe(true);
    expect(page.total).toBe(40);
  });

  it("reads quiz attempts with the same paging envelope", async () => {
    fetchMock.mockResolvedValue(jsonResponse(makePage([makeQuizAttemptSummary()])));

    const page = await api.getQuizAttempts({ limit: 5, offset: 0 });

    expect(page.items[0].quiz_id).toBe("99999999-9999-4999-8999-999999999999");
    const url = new URL(lastCall()[0]);
    expect(url.pathname).toBe("/api/v1/quizzes/attempts");
    expect(url.searchParams.get("limit")).toBe("5");
  });

  it("fetches one attempt enriched with review fields", async () => {
    fetchMock.mockResolvedValue(jsonResponse(makeQuizAttemptDetail()));

    const detail = await api.getQuizAttempt("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb");

    expect(detail.answers[0]).toMatchObject({
      option_a: "Nucleophile",
      option_b: "Electrophile",
      explanation: "A nucleophile attacks the electrophile.",
      source_document_title: "lecture-01.pdf",
      source_page_number: 4,
      source_snippet: "SN2 reactions proceed in a single step.",
    });
    expect(detail.topic).toBe("Nucleophilic substitution");
    const url = new URL(lastCall()[0]);
    expect(url.pathname).toBe("/api/v1/quizzes/attempts/bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb");
  });

  it("requests the analytics overview and returns the aggregate", async () => {
    fetchMock.mockResolvedValue(jsonResponse(makeAnalyticsOverview()));

    const overview = await api.getAnalyticsOverview({ subjectId: "subject-1", limit: 50 });

    expect(overview.totals.attempt_count).toBe(3);
    const url = new URL(lastCall()[0]);
    expect(url.pathname).toBe("/api/v1/analytics/overview");
    expect(url.searchParams.get("subject_id")).toBe("subject-1");
    expect(url.searchParams.get("limit")).toBe("50");
  });

  it("surfaces a forbidden overview instead of returning an empty aggregate", async () => {
    fetchMock.mockResolvedValue(jsonResponse({ detail: "Not found." }, 404));

    await expect(api.getAnalyticsOverview()).rejects.toThrow("Not found.");
  });
});

describe("api next-learning endpoints", () => {
  it("requests the next best action across all subjects", async () => {
    fetchMock.mockResolvedValue(jsonResponse({ status: "ok", recommendation: null }));

    await api.getNextLearning();

    expect(lastCall()[0]).toBe(`${BASE}/recommendations/next`);
  });

  it("requests the next best action within one subject", async () => {
    fetchMock.mockResolvedValue(jsonResponse({ status: "ok", recommendation: null }));

    await api.getSubjectNextLearning("subject-1");

    expect(lastCall()[0]).toBe(`${BASE}/recommendations/subjects/subject-1/next`);
  });
});

/**
 * B1/B2: the access token is short-lived, so a session is expected to outlive
 * it. These cover the silent refresh that keeps a signed-in student signed in
 * without the page ever holding a credential.
 */
describe("api session refresh", () => {
  function queued(...responses: Response[]) {
    responses.forEach((r) => fetchMock.mockResolvedValueOnce(r));
  }

  it("refreshes once and replays the original request after a 401", async () => {
    queued(
      jsonResponse({ detail: "Could not validate credentials" }, 401),
      jsonResponse({ access_token: "new" }),
      jsonResponse([{ id: "1" }]),
    );

    const result = await api.getSubjects();

    expect(result).toEqual([{ id: "1" }]);
    expect(fetchMock.mock.calls[1][0]).toBe(`${BASE}/auth/refresh`);
    expect((fetchMock.mock.calls[1][1] as RequestInit).method).toBe("POST");
  });

  it("only refreshes once, no matter how many requests fail together", async () => {
    // The dashboard fires several requests at once. Each rotating the refresh
    // token would invalidate the others.
    queued(
      jsonResponse({ detail: "no" }, 401),
      jsonResponse({ detail: "no" }, 401),
      jsonResponse({ detail: "no" }, 401),
      jsonResponse({ access_token: "new" }),
      jsonResponse([{ id: "1" }]),
      jsonResponse([{ id: "2" }]),
      jsonResponse([{ id: "3" }]),
    );

    await Promise.all([api.getSubjects(), api.getSubjects(), api.getSubjects()]);

    const refreshCalls = fetchMock.mock.calls.filter((c) => String(c[0]).endsWith("/auth/refresh"));
    expect(refreshCalls).toHaveLength(1);
  });

  it("surfaces the original error when the refresh is rejected", async () => {
    // A rejected refresh means the session is genuinely over, not a blip.
    queued(
      jsonResponse({ detail: "Could not validate credentials" }, 401),
      jsonResponse({ detail: "Invalid or expired refresh token" }, 401),
    );

    await expect(api.getSubjects()).rejects.toThrow("Could not validate credentials");
  });

  it("does not try to refresh a failed sign-in", async () => {
    // A 401 from /auth/login *is* the answer. Refreshing would turn "wrong
    // password" into a confusing second failure.
    queued(jsonResponse({ detail: "Incorrect email or password" }, 401));

    await expect(api.login({ email: "a@b.com", password: "nope" })).rejects.toThrow(
      "Incorrect email or password",
    );
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("does not loop when the retried request is rejected again", async () => {
    queued(
      jsonResponse({ detail: "no" }, 401),
      jsonResponse({ access_token: "new" }),
      jsonResponse({ detail: "still no" }, 401),
    );

    await expect(api.getSubjects()).rejects.toThrow("still no");
    expect(fetchMock).toHaveBeenCalledTimes(3);
  });
});

describe("api logout", () => {
  it("posts to the logout endpoint with cookies attached", async () => {
    fetchMock.mockResolvedValue(emptyResponse(204));

    await api.logout();

    const [url, call] = lastCall();
    expect(url).toBe(`${BASE}/auth/logout`);
    expect(call.method).toBe("POST");
    expect(call.credentials).toBe("include");
  });

  it("swallows the failure so the caller can still sign out locally", async () => {
    fetchMock.mockResolvedValue(jsonResponse({ detail: "boom" }, 500));

    await expect(api.logout()).resolves.toBeUndefined();
  });
});
