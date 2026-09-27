import type {
  Document,
  Flashcard,
  FlashcardDeck,
  FlashcardDeckSummary,
  FlashcardReviewSummary,
  FlashcardTopicProgress,
  Quiz,
  QuizQuestion,
  QuizResultResponse,
  Recommendation,
  StudyPlan,
  StudyPlanItem,
  Subject,
} from "@/lib/api";

/**
 * Deterministic factories for the API payload shapes in `src/lib/api.ts`.
 *
 * Every builder returns a complete, type-correct object so a test only has to
 * override the field it actually cares about.
 */

export function makeSubject(overrides: Partial<Subject> = {}): Subject {
  return {
    id: "11111111-1111-4111-8111-111111111111",
    user_id: "22222222-2222-4222-8222-222222222222",
    title: "Organic Chemistry",
    description: "Reaction mechanisms",
    color: "#0284c7",
    created_at: "2026-01-01T00:00:00Z",
    ...overrides,
  };
}

export function makeDocument(overrides: Partial<Document> = {}): Document {
  return {
    id: "33333333-3333-4333-8333-333333333333",
    subject_id: "11111111-1111-4111-8111-111111111111",
    user_id: "22222222-2222-4222-8222-222222222222",
    title: "lecture-01.pdf",
    file_size_bytes: 2048,
    status: "READY",
    error_message: null,
    chunk_count: 12,
    created_at: "2026-01-01T00:00:00Z",
    ...overrides,
  };
}

export function makeRecommendation(
  overrides: Partial<Recommendation> = {},
): Recommendation {
  return {
    subject_id: "11111111-1111-4111-8111-111111111111",
    subject_title: "Organic Chemistry",
    topic: "Nucleophilic substitution",
    priority: 82,
    mastery: 31,
    mastery_level: "novice",
    accuracy: 40,
    attempts: 2,
    questions_attempted: 10,
    questions_incorrect: 6,
    recommendation_type: "REVIEW",
    reason: "Low mastery with 6 missed questions.",
    suggested_action: "Review the source material, then retry.",
    suggested_difficulty: "medium",
    last_attempt_at: "2026-01-02T00:00:00Z",
    ...overrides,
  };
}

export function makePlanItem(overrides: Partial<StudyPlanItem> = {}): StudyPlanItem {
  return {
    id: "44444444-4444-4444-8444-444444444444",
    session_id: "55555555-5555-4555-8555-555555555555",
    topic: "Nucleophilic substitution",
    action: "PRACTICE",
    priority: 82,
    estimated_minutes: 30,
    reason: "Low mastery with 6 missed questions.",
    completed: false,
    completed_at: null,
    material_available: true,
    mastery: 31,
    accuracy: 40,
    attempts: 2,
    questions_attempted: 10,
    questions_incorrect: 6,
    suggested_difficulty: "medium",
    ...overrides,
  };
}

export function makeStudyPlan(overrides: Partial<StudyPlan> = {}): StudyPlan {
  return {
    id: "66666666-6666-4666-8666-666666666666",
    subject_id: "11111111-1111-4111-8111-111111111111",
    subject_title: "Organic Chemistry",
    generated_at: "2026-01-03T10:00:00Z",
    created_at: "2026-01-03T10:00:00Z",
    target_date: "2026-01-17",
    material_available: true,
    omitted_topic_count: 0,
    sessions: [
      {
        id: "55555555-5555-4555-8555-555555555555",
        session_number: 1,
        scheduled_date: "2026-01-05",
        estimated_minutes: 45,
        items: [makePlanItem()],
      },
    ],
    ...overrides,
  };
}

export function makeQuizQuestion(
  overrides: Partial<QuizQuestion> = {},
): QuizQuestion {
  return {
    id: "77777777-7777-4777-8777-777777777777",
    question_text: "Which reagent drives an SN2 reaction?",
    option_a: "Nucleophile",
    option_b: "Electrophile",
    option_c: "Chelating agent",
    option_d: "Reducing agent",
    difficulty: "medium",
    source_document_id: "33333333-3333-4333-8333-333333333333",
    source_document_title: "lecture-01.pdf",
    source_chunk_id: "88888888-8888-4888-8888-888888888888",
    source_page_number: 4,
    source_snippet: "SN2 reactions proceed in a single step.",
    ...overrides,
  };
}

export function makeQuiz(overrides: Partial<Quiz> = {}): Quiz {
  return {
    id: "99999999-9999-4999-8999-999999999999",
    subject_id: "11111111-1111-4111-8111-111111111111",
    title: "Organic Chemistry Quiz",
    difficulty: "medium",
    question_count: 2,
    topic: null,
    created_at: "2026-01-04T00:00:00Z",
    questions: [
      makeQuizQuestion(),
      makeQuizQuestion({
        id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        question_text: "What is the leaving group in SN1?",
      }),
    ],
    ...overrides,
  };
}

export function makeQuizResult(
  quiz: Quiz,
  overrides: Partial<QuizResultResponse> = {},
): QuizResultResponse {
  const first = quiz.questions[0];
  const second = quiz.questions[1] ?? quiz.questions[0];
  return {
    quiz_id: quiz.id,
    title: quiz.title,
    attempt_id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
    total_questions: quiz.questions.length,
    score: 1,
    correct_answers: 1,
    incorrect_answers: 1,
    percentage: 50,
    results: [
      {
        question_id: first.id,
        question_text: first.question_text,
        selected_option: "A",
        correct_option: "A",
        is_correct: true,
        explanation: "A nucleophile attacks the electrophile.",
        difficulty: "medium",
        source_document_id: first.source_document_id,
        source_document_title: first.source_document_title,
        source_chunk_id: first.source_chunk_id,
        source_page_number: first.source_page_number,
        source_snippet: first.source_snippet,
      },
      {
        question_id: second.id,
        question_text: second.question_text,
        selected_option: "B",
        correct_option: "C",
        is_correct: false,
        explanation: "The leaving group departs first.",
        difficulty: "medium",
        source_document_id: null,
        source_document_title: null,
        source_chunk_id: null,
        source_page_number: null,
        source_snippet: null,
      },
    ],
    ...overrides,
  };
}

export function makeFlashcard(overrides: Partial<Flashcard> = {}): Flashcard {
  return {
    id: "55555555-5555-4555-8555-555555555555",
    front_text: "What does a vector database index?",
    back_text: "High-dimensional embeddings, so similarity search stays fast.",
    hint: "Think about embeddings.",
    topic: "Vector Databases",
    position: 0,
    source_document_id: "33333333-3333-4333-8333-333333333333",
    source_document_title: "lecture-01.pdf",
    source_chunk_id: "66666666-6666-4666-8666-666666666666",
    source_page_number: 3,
    source_snippet: "Vector databases store high-dimensional embeddings.",
    review_count: 0,
    correct_count: 0,
    ...overrides,
  };
}

export function makeFlashcardDeck(overrides: Partial<FlashcardDeck> = {}): FlashcardDeck {
  return {
    id: "77777777-7777-4777-8777-777777777777",
    subject_id: "11111111-1111-4111-8111-111111111111",
    title: "Flashcards: Vector Databases",
    topic: "Vector Databases",
    card_count: 2,
    created_at: "2026-01-02T00:00:00Z",
    cards: [
      makeFlashcard({ position: 0 }),
      makeFlashcard({
        id: "88888888-8888-4888-8888-888888888888",
        position: 1,
        front_text: "What is retrieval augmented generation?",
        back_text: "Grounding a model's answer in retrieved source documents.",
        review_count: 2,
        correct_count: 1,
      }),
    ],
    ...overrides,
  };
}

export function makeFlashcardDeckSummary(
  overrides: Partial<FlashcardDeckSummary> = {},
): FlashcardDeckSummary {
  return {
    id: "77777777-7777-4777-8777-777777777777",
    subject_id: "11111111-1111-4111-8111-111111111111",
    title: "Flashcards: Vector Databases",
    topic: "Vector Databases",
    card_count: 2,
    created_at: "2026-01-02T00:00:00Z",
    reviewed_card_count: 1,
    correct_count: 1,
    ...overrides,
  };
}

export function makeFlashcardReviewSummary(
  overrides: Partial<FlashcardReviewSummary> = {},
): FlashcardReviewSummary {
  return {
    deck_id: "77777777-7777-4777-8777-777777777777",
    title: "Flashcards: Vector Databases",
    reviewed_count: 1,
    correct_count: 1,
    incorrect_count: 0,
    percentage: 100,
    results: [
      {
        card_id: "55555555-5555-4555-8555-555555555555",
        is_correct: true,
        response: null,
        review_count: 1,
        correct_count: 1,
      },
    ],
    ...overrides,
  };
}

export function makeFlashcardTopicProgress(
  overrides: Partial<FlashcardTopicProgress> = {},
): FlashcardTopicProgress {
  return {
    subject_id: "11111111-1111-4111-8111-111111111111",
    topic: "Vector Databases",
    card_count: 2,
    reviewed_count: 2,
    correct_count: 1,
    accuracy: 50,
    mastery_score: 62.5,
    mastery_level: "developing",
    ...overrides,
  };
}
