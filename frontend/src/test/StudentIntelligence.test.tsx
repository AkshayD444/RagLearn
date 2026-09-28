import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { SubjectPerformance, TopicMastery, WeakTopic } from "@/lib/api";

vi.mock("@/lib/api", () => ({
  api: { getSubjectPerformance: vi.fn(), getSubjectWeakTopics: vi.fn() },
}));

import { api } from "@/lib/api";
import { StudentIntelligence } from "@/components/analytics/StudentIntelligence";

const getSubjectPerformance = vi.mocked(api.getSubjectPerformance);
const getSubjectWeakTopics = vi.mocked(api.getSubjectWeakTopics);

function weakTopic(overrides: Partial<WeakTopic> = {}): WeakTopic {
  return {
    subject_id: "subject-1",
    topic: "Nucleophilic substitution",
    mastery_score: 31,
    mastery_level: "novice",
    accuracy: 40,
    attempts: 2,
    questions_attempted: 10,
    questions_correct: 4,
    questions_incorrect: 6,
    reasons: [
      {
        code: "repeated_mistakes",
        message: "6 incorrect answers across 2 attempts on this topic.",
      },
    ],
    confidence: "confirmed",
    last_attempt_at: "2026-01-02T09:30:00Z",
    ...overrides,
  };
}

function mastery(overrides: Partial<TopicMastery> = {}): TopicMastery {
  return {
    id: "dddddddd-dddd-4ddd-8ddd-dddddddddddd",
    subject_id: "subject-1",
    topic: "Nucleophilic substitution",
    attempts: 2,
    questions_attempted: 10,
    questions_correct: 4,
    questions_incorrect: 6,
    accuracy: 40,
    mastery_score: 31,
    mastery_level: "novice",
    last_attempt_at: "2026-01-02T09:30:00Z",
    updated_at: "2026-01-02T09:30:00Z",
    ...overrides,
  };
}

function performance(overrides: Partial<SubjectPerformance> = {}): SubjectPerformance {
  return {
    subject_id: "subject-1",
    summary: {
      total_attempts: 2,
      total_questions: 20,
      total_correct: 11,
      total_incorrect: 9,
      accuracy: 55,
      average_percentage: 58.3,
      best_percentage: 70,
      difficulty_breakdown: [
        { difficulty: "easy", attempted: 10, correct: 8, accuracy: 80 },
        { difficulty: "hard", attempted: 10, correct: 3, accuracy: 30 },
      ],
    },
    attempts: [
      {
        id: "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee",
        quiz_id: "quiz-1",
        subject_id: "subject-1",
        score: 7,
        total_questions: 10,
        percentage: 70,
        started_at: "2026-01-02T09:00:00Z",
        completed_at: "2026-01-02T09:30:00Z",
      },
      {
        id: "ffffffff-ffff-4fff-8fff-ffffffffffff",
        quiz_id: "quiz-2",
        subject_id: "subject-1",
        score: 4,
        total_questions: 10,
        percentage: 40,
        started_at: "2026-01-01T09:00:00Z",
        completed_at: "2026-01-01T09:20:00Z",
      },
    ],
    mastery: [mastery()],
    ...overrides,
  };
}

beforeEach(() => {
  getSubjectPerformance.mockReset();
  getSubjectWeakTopics.mockReset();
  getSubjectWeakTopics.mockResolvedValue([]);
});

describe("StudentIntelligence loading", () => {
  it("shows a loading state first", () => {
    getSubjectPerformance.mockReturnValue(new Promise(() => {}));

    render(<StudentIntelligence subjectId="subject-1" />);

    expect(screen.getByText("Loading your learning data...")).toBeInTheDocument();
  });

  it("requests performance for the given subject", async () => {
    getSubjectPerformance.mockResolvedValue(performance());

    render(<StudentIntelligence subjectId="subject-1" />);

    await waitFor(() => expect(getSubjectPerformance).toHaveBeenCalledWith("subject-1"));
  });

  it("refetches when refreshKey changes", async () => {
    getSubjectPerformance.mockResolvedValue(performance());

    const { rerender } = render(<StudentIntelligence subjectId="subject-1" refreshKey={0} />);
    await waitFor(() => expect(getSubjectPerformance).toHaveBeenCalledTimes(1));

    rerender(<StudentIntelligence subjectId="subject-1" refreshKey={1} />);
    await waitFor(() => expect(getSubjectPerformance).toHaveBeenCalledTimes(2));
  });

  it("refetches on demand via Refresh", async () => {
    getSubjectPerformance.mockResolvedValue(performance());

    render(<StudentIntelligence subjectId="subject-1" />);
    await screen.findByText("Topic Mastery");

    fireEvent.click(screen.getByRole("button", { name: /Refresh/ }));

    await waitFor(() => expect(getSubjectPerformance).toHaveBeenCalledTimes(2));
  });

  it("shows an error banner and the empty state when loading fails", async () => {
    getSubjectPerformance.mockRejectedValue(new Error("Subject not found."));

    render(<StudentIntelligence subjectId="subject-1" />);

    expect(await screen.findByText("Subject not found.")).toBeInTheDocument();
    expect(screen.getByText("No performance data yet")).toBeInTheDocument();
  });

  it("falls back to a generic load error", async () => {
    getSubjectPerformance.mockRejectedValue({});

    render(<StudentIntelligence subjectId="subject-1" />);

    expect(await screen.findByText("Failed to load performance data.")).toBeInTheDocument();
  });
});

describe("StudentIntelligence with no attempts", () => {
  it("prompts the user to submit a quiz", async () => {
    getSubjectPerformance.mockResolvedValue(
      performance({
        summary: { ...performance().summary, total_attempts: 0 },
      }),
    );

    render(<StudentIntelligence subjectId="subject-1" />);

    expect(await screen.findByText("No attempts recorded yet")).toBeInTheDocument();
    expect(screen.queryByText("Topic Mastery")).not.toBeInTheDocument();
  });
});

describe("StudentIntelligence summary", () => {
  it("renders the four summary cards", async () => {
    getSubjectPerformance.mockResolvedValue(performance());

    render(<StudentIntelligence subjectId="subject-1" />);

    const attemptsCard = (await screen.findByText("Attempts")).closest("div.p-4") as HTMLElement;
    expect(within(attemptsCard).getByText("2")).toBeInTheDocument();

    expect(screen.getByText("58.3%")).toBeInTheDocument();
    // Once for the Best Score card and once for the best recent attempt.
    expect(screen.getAllByText("70%")).toHaveLength(2);
    expect(screen.getByText("55%")).toBeInTheDocument();
  });

  it("renders the difficulty breakdown with computed accuracy", async () => {
    getSubjectPerformance.mockResolvedValue(performance());

    render(<StudentIntelligence subjectId="subject-1" />);

    expect(await screen.findByText("Difficulty Breakdown")).toBeInTheDocument();
    expect(screen.getByText("8/10 • 80%")).toBeInTheDocument();
    expect(screen.getByText("3/10 • 30%")).toBeInTheDocument();
  });

  it("reports 0% accuracy for a difficulty with no attempts", async () => {
    const base = performance();
    getSubjectPerformance.mockResolvedValue(
      performance({
        summary: {
          ...base.summary,
          difficulty_breakdown: [
            { difficulty: "medium", attempted: 0, correct: 0, accuracy: 0 },
          ],
        },
      }),
    );

    render(<StudentIntelligence subjectId="subject-1" />);

    expect(await screen.findByText("0/0 • 0%")).toBeInTheDocument();
  });

  it("hides the difficulty breakdown when there are no difficulty rows", async () => {
    const base = performance();
    getSubjectPerformance.mockResolvedValue(
      performance({
        summary: { ...base.summary, difficulty_breakdown: [] },
      }),
    );

    render(<StudentIntelligence subjectId="subject-1" />);

    await screen.findByText("Topic Mastery");
    expect(screen.queryByText("Difficulty Breakdown")).not.toBeInTheDocument();
  });
});

describe("StudentIntelligence topic mastery", () => {
  it("renders each topic with its level, accuracy, and score", async () => {
    getSubjectPerformance.mockResolvedValue(performance());

    render(<StudentIntelligence subjectId="subject-1" />);

    expect(await screen.findByText("Nucleophilic substitution")).toBeInTheDocument();
    expect(screen.getByText("4/10 • 40% accuracy")).toBeInTheDocument();
    expect(screen.getByText("novice")).toBeInTheDocument();
    expect(screen.getByText("31")).toBeInTheDocument();
    expect(screen.getByText(/2 attempts • last /)).toBeInTheDocument();
  });

  it("falls back to novice for a missing mastery level", async () => {
    getSubjectPerformance.mockResolvedValue(
      performance({ mastery: [mastery({ mastery_level: "" })] }),
    );

    render(<StudentIntelligence subjectId="subject-1" />);

    expect(await screen.findByText("novice")).toBeInTheDocument();
  });

  it("prompts for a quiz when there are no mastery rows", async () => {
    getSubjectPerformance.mockResolvedValue(performance({ mastery: [] }));

    render(<StudentIntelligence subjectId="subject-1" />);

    expect(
      await screen.findByText("Submit a quiz to start tracking mastery per topic."),
    ).toBeInTheDocument();
  });

  it("caps the mastery bar at 100%", async () => {
    getSubjectPerformance.mockResolvedValue(
      performance({ mastery: [mastery({ mastery_score: 140, topic: "Overflowing" })] }),
    );

    const { container } = render(<StudentIntelligence subjectId="subject-1" />);
    await screen.findByText("Overflowing");

    const bar = container.querySelector("div[style]") as HTMLElement;
    expect(bar.style.width).toBe("100%");
  });
});

describe("StudentIntelligence recent attempts", () => {
  it("renders each attempt score, percentage, and timestamp", async () => {
    getSubjectPerformance.mockResolvedValue(performance());

    render(<StudentIntelligence subjectId="subject-1" />);

    expect(await screen.findByText("Recent Attempts")).toBeInTheDocument();
    expect(screen.getByText("7 / 10 correct")).toBeInTheDocument();
    expect(screen.getByText("4 / 10 correct")).toBeInTheDocument();
    expect(screen.getAllByText("70%")).toHaveLength(2);
    expect(screen.getByText("40%")).toBeInTheDocument();
  });
});

describe("StudentIntelligence needs practice", () => {
  it("requests weak topics for the given subject", async () => {
    getSubjectPerformance.mockResolvedValue(performance());

    render(<StudentIntelligence subjectId="subject-1" />);

    await waitFor(() =>
      expect(getSubjectWeakTopics).toHaveBeenCalledWith("subject-1"),
    );
  });

  it("refetches weak topics when refreshKey changes", async () => {
    getSubjectPerformance.mockResolvedValue(performance());

    const { rerender } = render(<StudentIntelligence subjectId="subject-1" refreshKey={0} />);
    await waitFor(() => expect(getSubjectWeakTopics).toHaveBeenCalledTimes(1));

    rerender(<StudentIntelligence subjectId="subject-1" refreshKey={1} />);
    await waitFor(() => expect(getSubjectWeakTopics).toHaveBeenCalledTimes(2));
  });

  it("renders each weak topic with its accuracy and reasons", async () => {
    getSubjectPerformance.mockResolvedValue(performance());
    getSubjectWeakTopics.mockResolvedValue([weakTopic()]);

    render(<StudentIntelligence subjectId="subject-1" />);

    // The mastery fixture mirrors the weak-topic numbers, so the shared
    // "4/10 • 40% accuracy" string appears in both panels: scope to this one.
    const practiceCard = (await screen.findByText("Needs Practice")).closest(
      "div.p-4",
    ) as HTMLElement;

    expect(within(practiceCard).getByText("Repeated mistakes")).toBeInTheDocument();
    expect(
      within(practiceCard).getByText("6 incorrect answers across 2 attempts on this topic."),
    ).toBeInTheDocument();
    expect(within(practiceCard).getByText("4/10 • 40% accuracy")).toBeInTheDocument();
  });

  it("labels every reason code the backend can return", async () => {
    getSubjectPerformance.mockResolvedValue(performance());
    getSubjectWeakTopics.mockResolvedValue([
      weakTopic({
        topic: "Multi-flagged",
        reasons: [
          { code: "repeated_mistakes", message: "Three wrong answers." },
          { code: "low_accuracy", message: "Accuracy is 20%." },
          { code: "recent_poor_performance", message: "The last attempt dropped sharply." },
          { code: "insufficient_evidence", message: "Only 1 question answered." },
        ],
      }),
    ]);

    render(<StudentIntelligence subjectId="subject-1" />);

    expect(await screen.findByText("Repeated mistakes")).toBeInTheDocument();
    expect(screen.getByText("Low accuracy")).toBeInTheDocument();
    expect(screen.getByText("Recent decline")).toBeInTheDocument();
    expect(screen.getByText("Needs more evidence")).toBeInTheDocument();
  });

  it("labels a confirmed weakness as confirmed and a thin one as low confidence", async () => {
    getSubjectPerformance.mockResolvedValue(performance());
    getSubjectWeakTopics.mockResolvedValue([
      weakTopic({ topic: "Solid", confidence: "confirmed" }),
      weakTopic({ topic: "Thin", confidence: "low" }),
    ]);

    render(<StudentIntelligence subjectId="subject-1" />);

    expect(await screen.findByText("Solid")).toBeInTheDocument();
    expect(screen.getByText("Confirmed")).toBeInTheDocument();
    expect(screen.getByText("Low confidence")).toBeInTheDocument();
  });

  it("falls back to the raw code for an unknown reason", async () => {
    getSubjectPerformance.mockResolvedValue(performance());
    getSubjectWeakTopics.mockResolvedValue([
      weakTopic({ reasons: [{ code: "some_future_rule", message: "Because." }] }),
    ]);

    render(<StudentIntelligence subjectId="subject-1" />);

    expect(await screen.findByText("some_future_rule")).toBeInTheDocument();
  });

  it("shows a reassuring message when no weak topics are detected", async () => {
    getSubjectPerformance.mockResolvedValue(performance());
    getSubjectWeakTopics.mockResolvedValue([]);

    render(<StudentIntelligence subjectId="subject-1" />);

    expect(
      await screen.findByText(
        "No weak topics detected. Every topic you have answered looks solid.",
      ),
    ).toBeInTheDocument();
  });

  it("still renders performance when the weak topics request fails", async () => {
    getSubjectPerformance.mockResolvedValue(performance());
    getSubjectWeakTopics.mockRejectedValue(new Error("Weak topics unavailable."));

    render(<StudentIntelligence subjectId="subject-1" />);

    // The supplementary panel degrades quietly instead of taking over the view.
    expect(await screen.findByText("Topic Mastery")).toBeInTheDocument();
    expect(
      await screen.findByText(
        "No weak topics detected. Every topic you have answered looks solid.",
      ),
    ).toBeInTheDocument();
    expect(screen.queryByText("Weak topics unavailable.")).not.toBeInTheDocument();
  });

  it("hides the panel when there are no attempts at all", async () => {
    getSubjectPerformance.mockResolvedValue(
      performance({ summary: { ...performance().summary, total_attempts: 0 } }),
    );

    render(<StudentIntelligence subjectId="subject-1" />);

    expect(await screen.findByText("No attempts recorded yet")).toBeInTheDocument();
    expect(screen.queryByText("Needs Practice")).not.toBeInTheDocument();
  });
});
