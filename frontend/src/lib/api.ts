const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL || "http://localhost:8000/api/v1";

export interface User {
  id: string;
  email: string;
  full_name: string;
  created_at: string;
}

export interface AuthResponse {
  /**
   * B2: still returned by the API for non-browser clients, but the frontend
   * deliberately does not read or store it. The session lives in an HttpOnly
   * cookie that JavaScript cannot see. See `ApiClient.request`.
   */
  access_token: string;
  refresh_token?: string;
  expires_in?: number;
  token_type: string;
  user: User;
}

export interface Subject {
  id: string;
  user_id: string;
  title: string;
  description: string;
  color: string;
  created_at: string;
}

export interface SubjectUpdate {
  title?: string;
  description?: string;
  color?: string;
}

export interface Document {
  id: string;
  subject_id: string;
  user_id: string;
  title: string;
  file_size_bytes: number;
  status: "PROCESSING" | "READY" | "FAILED";
  error_message?: string | null;
  chunk_count: number;
  created_at: string;
}

/**
 * The narrow projection a status poll needs.
 *
 * Deliberately smaller than {@link Document}: a client watching an upload finish
 * asks for this on a timer, so it should not re-transfer the title, the byte
 * count, or anything else that does not change while processing runs.
 */
export interface DocumentStatus {
  id: string;
  subject_id: string;
  status: "PROCESSING" | "READY" | "FAILED";
  error_message?: string | null;
  chunk_count: number;
  created_at: string;
}

export interface RagSource {
  chunk_id: string;
  document_id: string;
  document_title: string;
  chunk_index: number;
  page_number: number;
  content: string;
  score: number;
}

export interface RagAnswerResponse {
  subject_id: string;
  question: string;
  answer: string;
  sources: RagSource[];
  model_used: string;
}

export type QuizDifficulty = "easy" | "medium" | "hard";

export interface QuizQuestion {
  id: string;
  question_text: string;
  option_a: string;
  option_b: string;
  option_c: string;
  option_d: string;
  difficulty: string;
  source_document_id: string | null;
  source_document_title: string | null;
  source_chunk_id: string | null;
  source_page_number: number | null;
  source_snippet: string | null;
}

export interface Quiz {
  id: string;
  subject_id: string;
  title: string;
  difficulty: string;
  question_count: number;
  topic: string | null;
  created_at: string;
  questions: QuizQuestion[];
}

export type SelectedOption = "A" | "B" | "C" | "D";

export interface AnswerItem {
  question_id: string;
  selected_option: SelectedOption;
  time_taken?: number | null;
}

export interface QuestionResult {
  question_id: string;
  question_text: string;
  selected_option: string;
  correct_option: string;
  is_correct: boolean;
  explanation: string;
  difficulty: string;
  source_document_id: string | null;
  source_document_title: string | null;
  source_chunk_id: string | null;
  source_page_number: number | null;
  source_snippet: string | null;
}

export interface QuizResultResponse {
  quiz_id: string;
  title: string;
  attempt_id: string | null;
  total_questions: number;
  score: number;
  correct_answers: number;
  incorrect_answers: number;
  percentage: number;
  results: QuestionResult[];
}

/**
 * One page of a history listing.
 *
 * `total` counts the whole filtered collection, not this page, and `has_more` is
 * derived from it. Callers can therefore never mistake a bounded page for the
 * full history and paginate correctly from either field.
 */
export interface Page<T> {
  items: T[];
  total: number;
  limit: number;
  offset: number;
  has_more: boolean;
}

/**
 * A quiz as listed in history: metadata plus how the student did on it.
 *
 * The attempt figures cover the whole history for this quiz, so they stay
 * correct on whichever page the quiz happens to appear.
 */
export interface QuizSummary {
  id: string;
  subject_id: string;
  subject_title: string | null;
  title: string;
  difficulty: string;
  question_count: number;
  topic: string | null;
  created_at: string;
  attempt_count: number;
  best_percentage: number;
  last_attempt_at: string | null;
}

export interface QuizDetail extends Omit<Quiz, "questions"> {
  questions: QuizQuestion[];
  attempt_count: number;
  best_percentage: number;
  last_attempt_at: string | null;
}

/** A scored attempt with the titles needed to render it without another call. */
export interface QuizAttemptSummary {
  id: string;
  quiz_id: string;
  subject_id: string;
  quiz_title: string;
  subject_title: string;
  score: number;
  total_questions: number;
  percentage: number;
  started_at: string | null;
  completed_at: string;
}

/**
 * The per-question snapshot stored with an attempt.
 *
 * `correct_answer` is exposed because an attempt row only exists after grading,
 * so this is a record of completed work, not an answer key for a live quiz.
 * The option texts, explanation, and source citation are enriched from the
 * still-persisted question row for review and fall back to null if that row is
 * gone.
 */
export interface QuizAttemptAnswer {
  id: string;
  question_id: string;
  question_text: string;
  selected_answer: string;
  correct_answer: string;
  is_correct: boolean;
  difficulty: string;
  topic: string | null;
  time_taken: number | null;
  option_a: string | null;
  option_b: string | null;
  option_c: string | null;
  option_d: string | null;
  explanation: string | null;
  source_document_title: string | null;
  source_page_number: number | null;
  source_snippet: string | null;
}

export interface QuizAttemptDetail extends QuizAttemptSummary {
  /** Set when every answer belongs to the same topic, so the review can offer
   * "practice this topic again". */
  topic: string | null;
  answers: QuizAttemptAnswer[];
}

export interface DifficultyBreakdown {
  difficulty: string;
  attempted: number;
  correct: number;
  accuracy: number;
}

export interface PerformanceSummary {
  total_attempts: number;
  total_questions: number;
  total_correct: number;
  total_incorrect: number;
  accuracy: number;
  average_percentage: number;
  best_percentage: number;
  difficulty_breakdown: DifficultyBreakdown[];
}

export interface QuizAttempt {
  id: string;
  quiz_id: string;
  subject_id: string;
  score: number;
  total_questions: number;
  percentage: number;
  started_at: string | null;
  completed_at: string;
}

export interface TopicMastery {
  id: string;
  subject_id: string;
  topic: string;
  attempts: number;
  questions_attempted: number;
  questions_correct: number;
  questions_incorrect: number;
  accuracy: number;
  mastery_score: number;
  mastery_level: string;
  last_attempt_at: string | null;
  updated_at: string;
}

export interface SubjectPerformance {
  subject_id: string;
  summary: PerformanceSummary;
  attempts: QuizAttempt[];
  mastery: TopicMastery[];
}

/**
 * Lifetime totals for a student's learning.
 *
 * These describe the entire history regardless of how many rows exist or how
 * many the overview's `limit` returned, so the headline numbers never shift just
 * because a timeline page is shorter. `accuracy` and `average_percentage` are
 * both 0-100.
 */
export interface LearningTotals {
  subject_count: number;
  quiz_count: number;
  attempt_count: number;
  questions_answered: number;
  questions_correct: number;
  questions_incorrect: number;
  accuracy: number;
  average_percentage: number;
  best_percentage: number;
  /** Counts of non-deleted (active) subjects only, scoped like the rest. */
  document_count: number;
  ready_document_count: number;
  chunk_count: number;
  /** Cards currently in the review queue, never-studied cards included. */
  due_flashcard_count: number;
  /** Mean per-topic mastery across every answered topic, 0-100. */
  average_topic_mastery: number;
  first_activity_at: string | null;
  last_activity_at: string | null;
}

/** One row of the progress timeline: a single completed attempt. */
export interface LearningTimelineEntry {
  id: string;
  quiz_id: string;
  quiz_title: string;
  subject_id: string;
  subject_title: string;
  score: number;
  total_questions: number;
  percentage: number;
  completed_at: string;
}

export interface SubjectActivity {
  subject_id: string;
  subject_title: string;
  attempt_count: number;
  average_percentage: number;
  best_percentage: number;
  last_attempt_at: string | null;
}

/**
 * Dashboard-level learning history.
 *
 * The three lists are each capped by the request's `limit`; `totals` always
 * describes everything. `weakest_topics` reuses the same mastery rows the
 * per-subject analytics already return, rather than a second progress model.
 */
export interface AnalyticsOverview {
  totals: LearningTotals;
  timeline: LearningTimelineEntry[];
  subjects: SubjectActivity[];
  weakest_topics: TopicMastery[];
}

export type WeakReasonCode =
  | "repeated_mistakes"
  | "low_accuracy"
  | "recent_poor_performance"
  | "insufficient_evidence";

export interface WeakReason {
  /**
   * A code the backend can add at any time. The `string` arm keeps unknown
   * rules representable so a newer backend degrades to the raw code instead of
   * breaking the type contract; the union still drives editor completion.
   */
  code: WeakReasonCode | (string & {});
  message: string;
}

export interface WeakTopic {
  subject_id: string;
  topic: string;
  mastery_score: number;
  mastery_level: string;
  accuracy: number;
  attempts: number;
  questions_attempted: number;
  questions_correct: number;
  questions_incorrect: number;
  reasons: WeakReason[];
  /** `"confirmed"` when an aggregate-count rule fired, `"low"` otherwise. */
  confidence: string;
  last_attempt_at: string | null;
}

export type NextLearningActivity = "study" | "quiz" | "flashcards";

/**
 * The winner of the next-learning selection, with its chosen activity.
 *
 * `activity_type` is one of the three actions the app can actually deliver;
 * `due_cards` is how many cards a flashcard recommendation will present.
 */
export interface NextLearningRecommendation {
  subject_id: string;
  subject_title: string;
  topic: string;
  priority: number;
  mastery: number;
  mastery_level: string;
  accuracy: number;
  weighted_accuracy: number;
  difficulty_factor: number;
  questions_attempted: number;
  activity_type: NextLearningActivity;
  reason: string;
  suggested_difficulty: QuizDifficulty;
  due_cards: number;
  last_attempt_at: string | null;
}

/**
 * The statused envelope around the recommendation.
 *
 * `status` is `"ok"` with a `recommendation` present, or
 * `"insufficient_data"` with `recommendation` null because there is no
 * learning history yet; the client renders one empty state instead of
 * branching on a missing schema.
 */
export interface NextLearningRead {
  status: "ok" | "insufficient_data";
  reason: string;
  recommendation: NextLearningRecommendation | null;
}

export type RecommendationType = "REVIEW" | "PRACTICE" | "MAINTAIN";

export interface Recommendation {
  subject_id: string;
  subject_title: string;
  topic: string;
  priority: number;
  mastery: number;
  mastery_level: string;
  accuracy: number;
  attempts: number;
  questions_attempted: number;
  questions_incorrect: number;
  recommendation_type: RecommendationType;
  reason: string;
  suggested_action: string;
  suggested_difficulty: QuizDifficulty;
  last_attempt_at: string | null;
}

export type StudyPlanAction = "LEARN" | "PRACTICE" | "REVIEW" | "MAINTAIN";

export interface StudyPlanItem {
  id: string;
  session_id: string;
  topic: string;
  action: StudyPlanAction;
  priority: number;
  estimated_minutes: number;
  reason: string;
  completed: boolean;
  completed_at: string | null;
  material_available: boolean;
  mastery: number;
  accuracy: number;
  attempts: number;
  questions_attempted: number;
  questions_incorrect: number;
  suggested_difficulty: QuizDifficulty;
}

export interface StudyPlanSession {
  id: string;
  session_number: number;
  scheduled_date: string | null;
  estimated_minutes: number;
  items: StudyPlanItem[];
}

export interface StudyPlan {
  id: string;
  subject_id: string;
  subject_title: string;
  generated_at: string;
  created_at: string;
  target_date: string | null;
  material_available: boolean;
  omitted_topic_count: number;
  sessions: StudyPlanSession[];
}

export interface StudyPlanGenerateOptions {
  subject_id: string;
  target_date?: string | null;
  available_minutes_per_day?: number | null;
  requested_sessions?: number | null;
  max_topics_per_session?: number | null;
}

export interface FlashcardSchedule {
  ease_factor: number;
  interval_days: number;
  repetitions: number;
  lapses: number;
  /** `null` means the card has never been studied, which counts as due now. */
  due_at: string | null;
  last_reviewed_at: string | null;
  is_new: boolean;
}

export interface Flashcard {
  id: string;
  front_text: string;
  back_text: string;
  hint?: string | null;
  topic?: string | null;
  position: number;
  source_document_id?: string | null;
  source_document_title?: string | null;
  source_chunk_id?: string | null;
  source_page_number?: number | null;
  source_snippet?: string | null;
  review_count: number;
  correct_count: number;
  schedule: FlashcardSchedule;
  /**
   * Deck and subject identity, echoed on every card.
   *
   * A card seen outside its deck response would otherwise be unaddressable: the
   * review endpoint takes a deck id plus one result per card, and a
   * cross-subject due queue has to be grouped by deck client-side.
   */
  deck_id: string;
  deck_title: string;
  subject_id: string;
}

export interface FlashcardDeck {
  id: string;
  subject_id: string;
  title: string;
  topic?: string | null;
  card_count: number;
  due_count: number;
  created_at: string;
  cards: Flashcard[];
}

export interface FlashcardDeckSummary {
  id: string;
  subject_id: string;
  title: string;
  topic?: string | null;
  card_count: number;
  due_count: number;
  created_at: string;
  reviewed_card_count: number;
  correct_count: number;
}

export interface FlashcardReviewItem {
  card_id: string;
  is_correct: boolean;
  response?: string | null;
}

export interface FlashcardReviewOutcome extends FlashcardReviewItem {
  review_count: number;
  correct_count: number;
  schedule: FlashcardSchedule;
}

export interface FlashcardReviewSummary {
  deck_id: string;
  title: string;
  reviewed_count: number;
  correct_count: number;
  incorrect_count: number;
  percentage: number;
  results: FlashcardReviewOutcome[];
  due_count: number;
  new_count: number;
}

/**
 * One page of the review queue.
 *
 * `due_count` is the size of the whole queue, so it can exceed `cards.length`
 * when the page is deliberately shorter. `new_count` is how much of that is
 * never-studied material.
 */
export interface FlashcardDueResponse {
  cards: Flashcard[];
  due_count: number;
  new_count: number;
}

export interface FlashcardRescheduleResponse {
  card_id: string;
  schedule: FlashcardSchedule;
}

export interface FlashcardTopicProgress {
  subject_id: string;
  topic: string;
  card_count: number;
  reviewed_count: number;
  correct_count: number;
  accuracy: number;
  /** Quiz-derived mastery. Never written by a card review. */
  mastery_score?: number | null;
  mastery_level?: string | null;
  /** Flashcard-derived mastery, tracked in its own evidence channel. */
  flashcard_mastery_score?: number | null;
  due_count: number;
}

// B2: the session is delivered in an HttpOnly cookie, so the page never holds a
// credential. Three consequences, all handled here.
//
// 1. `credentials: "include"` -- the browser only attaches the cookie to a
//    cross-origin request when asked to.
// 2. A CSRF header on state-changing requests. The server refuses a
//    cookie-authenticated write that does not echo the readable `csrf_token`
//    cookie, because a cross-origin page can make the browser send the cookie
//    but cannot read it or set a custom header.
// 3. `Authorization` is no longer set at all. The old code read the token back
//    out of localStorage on every call, which is exactly the exposure B2 removes.
const CSRF_COOKIE = "csrf_token";
const CSRF_HEADER = "X-CSRF-Token";
const UNSAFE_METHODS = new Set(["POST", "PUT", "PATCH", "DELETE"]);

// A 401 from one of these is the answer, not an expired session, so they must
// not trigger a refresh-and-retry that would mask the real error.
const NO_REFRESH_PATHS = [
  "/auth/login",
  "/auth/login/form",
  "/auth/register",
  "/auth/refresh",
  "/auth/change-password",
];

type UnauthorizedListener = () => void;
let unauthorizedListener: UnauthorizedListener | null = null;

/**
 * Registers a callback invoked when the session turns out to be unrecoverable.
 *
 * The store uses this to drop the signed-in UI when a refresh token has expired
 * or been revoked; without it a page would keep rendering a session the server
 * no longer recognises.
 */
export function setUnauthorizedListener(listener: UnauthorizedListener | null): void {
  unauthorizedListener = listener;
}

function readCookie(name: string): string | null {
  if (typeof document === "undefined") return null;
  const match = document.cookie.match(new RegExp(`(?:^|; )${name}=([^;]*)`));
  return match ? decodeURIComponent(match[1]) : null;
}

class ApiClient {
  // Shared across concurrent 401s so a dashboard that fires six requests at once
  // triggers one refresh, not six. Six would rotate the refresh token six times
  // and five of them would be rejected as replays.
  private refreshInFlight: Promise<boolean> | null = null;

  private csrfHeaders(method: string | undefined): Record<string, string> {
    if (!method || !UNSAFE_METHODS.has(method.toUpperCase())) return {};
    const token = readCookie(CSRF_COOKIE);
    return token ? { [CSRF_HEADER]: token } : {};
  }

  private async request<T>(
    endpoint: string,
    options: RequestInit = {},
    isRetry = false
  ): Promise<T> {
    const headers: Record<string, string> = {
      ...(options.headers as Record<string, string>),
      ...this.csrfHeaders(options.method),
    };

    if (!(options.body instanceof FormData)) {
      headers["Content-Type"] = "application/json";
    }

    const response = await fetch(`${API_BASE_URL}${endpoint}`, {
      ...options,
      headers,
      credentials: "include",
    });

    if (response.status === 401 && !isRetry && !NO_REFRESH_PATHS.some((p) => endpoint.startsWith(p))) {
      if (await this.refreshSession()) {
        return this.request<T>(endpoint, options, true);
      }
      // The refresh token is gone or was revoked server-side, so this is not a
      // transient failure: the session is over and the UI has to hear about it.
      unauthorizedListener?.();
    }

    if (!response.ok) {
      const errorData = await response.json().catch(() => ({ detail: "Request failed" }));
      throw new Error(errorData.detail || `HTTP Error ${response.status}`);
    }

    if (response.status === 204) {
      return {} as T;
    }

    return response.json();
  }

  /**
   * Trades the HttpOnly refresh cookie for a new pair, at most one attempt in
   * flight. Resolves false rather than throwing: a failed refresh is an
   * expected outcome, not an error the caller needs to distinguish.
   */
  private async refreshSession(): Promise<boolean> {
    if (!this.refreshInFlight) {
      this.refreshInFlight = (async () => {
        try {
          const response = await fetch(`${API_BASE_URL}/auth/refresh`, {
            method: "POST",
            headers: { ...this.csrfHeaders("POST") },
            credentials: "include",
          });
          return response.ok;
        } catch {
          // Offline or the API is down. Treated the same as a rejected
          // refresh: the caller surfaces the original 401.
          return false;
        } finally {
          this.refreshInFlight = null;
        }
      })();
    }
    return this.refreshInFlight;
  }

  // Auth Endpoints
  async register(payload: { email: string; full_name: string; password: string }): Promise<AuthResponse> {
    return this.request<AuthResponse>("/auth/register", {
      method: "POST",
      body: JSON.stringify(payload),
    });
  }

  async login(payload: { email: string; password: string }): Promise<AuthResponse> {
    return this.request<AuthResponse>("/auth/login", {
      method: "POST",
      body: JSON.stringify(payload),
    });
  }

  async logout(): Promise<void> {
    // Best effort: the local session is cleared either way, and a logout that
    // fails to reach the server still leaves the cookies to expire on their own.
    try {
      await this.request<Record<string, never>>("/auth/logout", { method: "POST" });
    } catch {
      // Intentionally ignored.
    }
  }

  async getMe(): Promise<User> {
    return this.request<User>("/auth/me");
  }

  // Subject Endpoints
  async getSubjects(): Promise<Subject[]> {
    return this.request<Subject[]>("/subjects");
  }

  async getSubject(id: string): Promise<Subject> {
    return this.request<Subject>(`/subjects/${id}`);
  }

  async createSubject(payload: { title: string; description?: string; color?: string }): Promise<Subject> {
    return this.request<Subject>("/subjects", {
      method: "POST",
      body: JSON.stringify(payload),
    });
  }

  /**
   * Partially updates a subject.
   *
   * Only the keys present in `payload` are sent, so renaming a subject leaves
   * its description and colour alone.
   */
  async updateSubject(id: string, payload: SubjectUpdate): Promise<Subject> {
    return this.request<Subject>(`/subjects/${id}`, {
      method: "PATCH",
      body: JSON.stringify(payload),
    });
  }

  async deleteSubject(id: string): Promise<void> {
    return this.request<void>(`/subjects/${id}`, {
      method: "DELETE",
    });
  }

  // Document Endpoints
  async uploadDocument(subjectId: string, file: File): Promise<Document> {
    const formData = new FormData();
    formData.append("subject_id", subjectId);
    formData.append("file", file);

    return this.request<Document>("/documents/upload", {
      method: "POST",
      body: formData,
    });
  }

  async getSubjectDocuments(subjectId: string): Promise<Document[]> {
    return this.request<Document[]>(`/documents/subject/${subjectId}`);
  }

  async getDocument(documentId: string): Promise<Document> {
    return this.request<Document>(`/documents/${documentId}`);
  }

  /**
   * Reads one document's processing state.
   *
   * The cheap target for a poll loop: it is the smallest response that can tell
   * a caller the document finished, failed, or is still working.
   */
  async getDocumentStatus(documentId: string): Promise<DocumentStatus> {
    return this.request<DocumentStatus>(`/documents/${documentId}/status`);
  }

  /**
   * Re-runs extraction and embedding for an existing document.
   *
   * Reuses the same document row, so a retry never leaves a duplicate behind.
   * Rejects with the API's 409 when a run is already in flight.
   */
  async reprocessDocument(documentId: string): Promise<DocumentStatus> {
    return this.request<DocumentStatus>(`/documents/${documentId}/reprocess`, {
      method: "POST",
    });
  }

  async deleteDocument(documentId: string): Promise<void> {
    return this.request<void>(`/documents/${documentId}`, {
      method: "DELETE",
    });
  }

  // RAG Tutor Endpoints
  async askRagTutor(
    subjectId: string,
    question: string,
    top_k = 5,
  ): Promise<RagAnswerResponse> {
    return this.request<RagAnswerResponse>("/rag/ask", {
      method: "POST",
      body: JSON.stringify({ subject_id: subjectId, question, top_k }),
    });
  }

  // Quiz Endpoints
  async generateQuiz(
    subjectId: string,
    questionCount: number,
    difficulty: QuizDifficulty,
    topic?: string | null,
  ): Promise<Quiz> {
    return this.request<Quiz>("/quizzes/generate", {
      method: "POST",
      body: JSON.stringify({
        subject_id: subjectId,
        question_count: questionCount,
        difficulty,
        topic: topic || null,
      }),
    });
  }

  async submitQuiz(
    quizId: string,
    answers: AnswerItem[],
    options?: { startedAt?: string | null },
  ): Promise<QuizResultResponse> {
    return this.request<QuizResultResponse>(`/quizzes/${quizId}/submit`, {
      method: "POST",
      body: JSON.stringify({
        answers,
        ...(options?.startedAt ? { started_at: options.startedAt } : {}),
      }),
    });
  }

  /**
   * Fetches one owned quiz with its questions and attempt aggregates.
   *
   * A soft-deleted quiz, or one belonging to somebody else, is a 404.
   */
  async getQuiz(quizId: string): Promise<QuizDetail> {
    return this.request<QuizDetail>(`/quizzes/${quizId}`);
  }

  /**
   * Lists owned quizzes, newest first.
   *
   * The response is a bounded page: `total` and `has_more` describe the whole
   * collection so callers paginate from them rather than from `items.length`.
   */
  async getQuizzes(options: { subjectId?: string; limit?: number; offset?: number } = {}): Promise<Page<QuizSummary>> {
    const params = new URLSearchParams();
    if (options.subjectId) params.set("subject_id", options.subjectId);
    if (options.limit !== undefined) params.set("limit", String(options.limit));
    if (options.offset !== undefined) params.set("offset", String(options.offset));
    const query = params.toString();
    return this.request<Page<QuizSummary>>(`/quizzes${query ? `?${query}` : ""}`);
  }

  /** Lists owned quiz attempts, most recent first. Survives quiz deletion. */
  async getQuizAttempts(
    options: { subjectId?: string; limit?: number; offset?: number } = {},
  ): Promise<Page<QuizAttemptSummary>> {
    const params = new URLSearchParams();
    if (options.subjectId) params.set("subject_id", options.subjectId);
    if (options.limit !== undefined) params.set("limit", String(options.limit));
    if (options.offset !== undefined) params.set("offset", String(options.offset));
    const query = params.toString();
    return this.request<Page<QuizAttemptSummary>>(`/quizzes/attempts${query ? `?${query}` : ""}`);
  }

  /** Fetches one owned attempt with every answer it recorded. */
  async getQuizAttempt(attemptId: string): Promise<QuizAttemptDetail> {
    return this.request<QuizAttemptDetail>(`/quizzes/attempts/${attemptId}`);
  }

  // Analytics / Student Intelligence Endpoints
  async getSubjectPerformance(subjectId: string): Promise<SubjectPerformance> {
    return this.request<SubjectPerformance>(`/analytics/subjects/${subjectId}/performance`);
  }

  /**
   * Fetches cross-subject learning history: totals, a recent timeline, per
   * subject activity, and the weakest topics.
   *
   * `totals` always describes the whole history; `limit` caps the three lists.
   * Pass `subjectId` to scope the entire response to one subject.
   */
  async getAnalyticsOverview(
    options: { subjectId?: string; limit?: number } = {},
  ): Promise<AnalyticsOverview> {
    const params = new URLSearchParams();
    if (options.subjectId) params.set("subject_id", options.subjectId);
    if (options.limit !== undefined) params.set("limit", String(options.limit));
    const query = params.toString();
    return this.request<AnalyticsOverview>(`/analytics/overview${query ? `?${query}` : ""}`);
  }

  async getSubjectMastery(subjectId: string): Promise<TopicMastery[]> {
    return this.request<TopicMastery[]>(`/analytics/subjects/${subjectId}/mastery`);
  }

  async getSubjectWeakTopics(subjectId: string, limit = 20): Promise<WeakTopic[]> {
    return this.request<WeakTopic[]>(
      `/analytics/subjects/${subjectId}/weak-topics?limit=${limit}`,
    );
  }

  // Adaptive Learning Endpoints
  async getSubjectRecommendations(subjectId: string, limit = 5): Promise<Recommendation[]> {
    return this.request<Recommendation[]>(`/recommendations/subjects/${subjectId}?limit=${limit}`);
  }

  async getTopRecommendations(limit = 5): Promise<Recommendation[]> {
    return this.request<Recommendation[]>(`/recommendations/top?limit=${limit}`);
  }

  /** Highest-priority next step across all subjects (see `/recommendations/next`). */
  async getNextLearning(): Promise<NextLearningRead> {
    return this.request<NextLearningRead>("/recommendations/next");
  }

  /** Highest-priority next step within one subject. */
  async getSubjectNextLearning(subjectId: string): Promise<NextLearningRead> {
    return this.request<NextLearningRead>(`/recommendations/subjects/${subjectId}/next`);
  }

  async generateStudyPlan(payload: StudyPlanGenerateOptions): Promise<StudyPlan> {
    return this.request<StudyPlan>("/study-plans/generate", {
      method: "POST",
      body: JSON.stringify(payload),
    });
  }

  async getLatestStudyPlan(subjectId?: string): Promise<StudyPlan | null> {
    const query = subjectId ? `?subject_id=${encodeURIComponent(subjectId)}` : "";
    try {
      return await this.request<StudyPlan>(`/study-plans/latest${query}`);
    } catch (error) {
      if (error instanceof Error && error.message === "No study plan found.") {
        return null;
      }
      throw error;
    }
  }

  async completeStudyPlanItem(itemId: string, completed = true): Promise<StudyPlan> {
    return this.request<StudyPlan>(`/study-plans/items/${itemId}/complete`, {
      method: "PATCH",
      body: JSON.stringify({ completed }),
    });
  }

  // Flashcard Endpoints
  async generateFlashcards(
    subjectId: string,
    cardCount = 10,
    topic?: string | null,
  ): Promise<FlashcardDeck> {
    return this.request<FlashcardDeck>("/flashcards/generate", {
      method: "POST",
      body: JSON.stringify({
        subject_id: subjectId,
        card_count: cardCount,
        topic: topic || null,
      }),
    });
  }

  async getFlashcardDecks(subjectId: string): Promise<FlashcardDeckSummary[]> {
    return this.request<FlashcardDeckSummary[]>(`/flashcards/decks?subject_id=${subjectId}`);
  }

  async getFlashcardDeck(deckId: string): Promise<FlashcardDeck> {
    return this.request<FlashcardDeck>(`/flashcards/decks/${deckId}`);
  }

  async deleteFlashcardDeck(deckId: string): Promise<void> {
    return this.request<void>(`/flashcards/decks/${deckId}`, {
      method: "DELETE",
    });
  }

  async reviewFlashcardDeck(
    deckId: string,
    reviews: FlashcardReviewItem[],
  ): Promise<FlashcardReviewSummary> {
    return this.request<FlashcardReviewSummary>(`/flashcards/decks/${deckId}/review`, {
      method: "POST",
      body: JSON.stringify({ reviews }),
    });
  }

  async getFlashcardProgress(subjectId: string): Promise<FlashcardTopicProgress[]> {
    return this.request<FlashcardTopicProgress[]>(
      `/flashcards/subjects/${subjectId}/progress`,
    );
  }

  /**
   * Fetches the review queue, most overdue first.
   *
   * `limit` is applied to the page, not the count: `due_count` on the response
   * always reflects the full queue, so "12 due" can be shown while 20 are
   * displayed. Every filter is optional, and an omitted filter means "all".
   */
  async getDueFlashcards(options: {
    subjectId?: string;
    deckId?: string;
    limit?: number;
  } = {}): Promise<FlashcardDueResponse> {
    const params = new URLSearchParams();
    if (options.subjectId) params.set("subject_id", options.subjectId);
    if (options.deckId) params.set("deck_id", options.deckId);
    if (options.limit !== undefined) params.set("limit", String(options.limit));
    const query = params.toString();
    return this.request<FlashcardDueResponse>(
      `/flashcards/due${query ? `?${query}` : ""}`,
    );
  }

  /**
   * Returns one card to the due queue, discarding its schedule.
   *
   * The escape hatch for a card the student is genuinely stuck on: the next
   * review starts from the initial state instead of being pushed a year out by
   * a stretched interval. Review history is preserved server-side.
   */
  async rescheduleFlashcard(cardId: string): Promise<FlashcardRescheduleResponse> {
    return this.request<FlashcardRescheduleResponse>(
      `/flashcards/cards/${cardId}/reschedule`,
      { method: "POST" },
    );
  }
}

export const api = new ApiClient();
