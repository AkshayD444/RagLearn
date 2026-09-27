import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { HistorySection } from "@/components/history/HistorySection";
import { api } from "@/lib/api";
import {
  makeAnalyticsOverview,
  makeLearningTotals,
  makePage,
  makeQuizAttemptSummary,
  makeQuizSummary,
} from "./factories";

function stubApi(overrides: Partial<Record<keyof typeof api, unknown>> = {}) {
  const methods = {
    getAnalyticsOverview: vi.fn().mockResolvedValue(makeAnalyticsOverview()),
    getQuizzes: vi.fn().mockResolvedValue(makePage([makeQuizSummary()])),
    getQuizAttempts: vi.fn().mockResolvedValue(makePage([makeQuizAttemptSummary()])),
    ...overrides,
  };
  Object.assign(api, methods);
  return methods;
}

/** The quiz list only, so a title shared with the timeline is unambiguous. */
function quizList() {
  return screen.getByTestId("history-list-quizzes");
}

function attemptList() {
  return screen.getByTestId("history-list-attempts");
}

describe("HistorySection", () => {
  beforeEach(() => {
    stubApi();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("shows a spinner while the first load is in flight", () => {
    stubApi({
      getAnalyticsOverview: vi.fn().mockReturnValue(new Promise(() => {})),
      getQuizzes: vi.fn().mockReturnValue(new Promise(() => {})),
    });

    const { container } = render(<HistorySection />);

    expect(container.querySelectorAll(".animate-spin").length).toBeGreaterThan(0);
    expect(screen.queryByText("Your learning history")).not.toBeNull();
  });

  it("renders lifetime totals, a timeline entry, and the quiz list", async () => {
    render(<HistorySection />);

    await waitFor(() => expect(screen.getByText("3 attempts")).toBeInTheDocument());
    // The stat tiles read out the aggregate, not the page.
    expect(screen.getByText("15 questions answered")).toBeInTheDocument();
    expect(screen.getByText("70%")).toBeInTheDocument();
    expect(screen.getByText("73.3%")).toBeInTheDocument();
    expect(screen.getByText("Recent activity")).toBeInTheDocument();
    // The same quiz appears both on the timeline and in the quiz list.
    expect(within(quizList()).getByText("Organic Chemistry Quiz")).toBeInTheDocument();
    expect(screen.getAllByText("Organic Chemistry Quiz").length).toBeGreaterThanOrEqual(2);
  });

  it("surfaces a summary error without hiding the rest of the section", async () => {
    stubApi({
      getAnalyticsOverview: vi.fn().mockRejectedValue(new Error("Overview unavailable.")),
    });

    render(<HistorySection />);

    await waitFor(() => expect(screen.getByText("Overview unavailable.")).toBeInTheDocument());
    // The history list still loads: one failing panel does not blank the page.
    expect(await screen.findByTestId("history-list-quizzes")).toBeInTheDocument();
  });

  it("explains an empty history instead of rendering blank panels", async () => {
    stubApi({
      getAnalyticsOverview: vi.fn().mockResolvedValue(
        makeAnalyticsOverview({
          totals: makeLearningTotals({
            subject_count: 0,
            quiz_count: 0,
            attempt_count: 0,
            questions_answered: 0,
            questions_correct: 0,
            questions_incorrect: 0,
            accuracy: 0,
            average_percentage: 0,
            best_percentage: 0,
            first_activity_at: null,
            last_activity_at: null,
          }),
          timeline: [],
          subjects: [],
          weakest_topics: [],
        }),
      ),
      getQuizzes: vi.fn().mockResolvedValue(makePage([])),
    });

    render(<HistorySection />);

    expect(await screen.findByText(/Nothing here yet/)).toBeInTheDocument();
    expect(screen.getByText("No quizzes generated yet.")).toBeInTheDocument();
    expect(screen.queryByText("Recent activity")).not.toBeInTheDocument();
  });

  it("switches between quiz history and attempt history", async () => {
    render(<HistorySection />);
    await screen.findByTestId("history-list-quizzes");

    fireEvent.click(screen.getByRole("tab", { name: "Attempts" }));

    await waitFor(() => expect(api.getQuizAttempts).toHaveBeenCalled());
    // The attempt row carries the score, the subject, and the completion date.
    expect(attemptList()).toHaveTextContent("Organic Chemistry");
    expect(attemptList()).toHaveTextContent("4/5");
    expect(attemptList()).toHaveTextContent("80%");
    expect(screen.queryByTestId("history-list-quizzes")).not.toBeInTheDocument();
  });

  it("requests the first page and enables Next only when more exists", async () => {
    const quizzes = Array.from({ length: 2 }, (_, i) =>
      makeQuizSummary({ id: `quiz-${i}`, title: `Quiz ${i}` }),
    );
    stubApi({
      getQuizzes: vi.fn().mockResolvedValue(makePage(quizzes, { total: 5, limit: 2, offset: 0 })),
    });

    render(<HistorySection pageSize={2} />);
    await screen.findByTestId("history-list-quizzes");

    expect(api.getQuizzes).toHaveBeenCalledWith({
      subjectId: undefined,
      limit: 2,
      offset: 0,
    });
    expect(screen.getByTestId("Quiz history-range")).toHaveTextContent("Showing 1–2 of 5");
    expect(screen.getByLabelText("Previous page of quiz history")).toBeDisabled();
    expect(screen.getByLabelText("Next page of quiz history")).toBeEnabled();
  });

  it("walks pages forwards and back, staying bounded", async () => {
    const quizzes = Array.from({ length: 2 }, (_, i) =>
      makeQuizSummary({ id: `quiz-${i}`, title: `Quiz ${i}` }),
    );
    const getQuizzes = vi
      .fn()
      .mockResolvedValueOnce(makePage(quizzes, { total: 5, limit: 2, offset: 0 }))
      .mockResolvedValueOnce(
        makePage([makeQuizSummary({ id: "quiz-2", title: "Quiz 2" })], {
          total: 5,
          limit: 2,
          offset: 2,
        }),
      )
      .mockResolvedValueOnce(makePage(quizzes, { total: 5, limit: 2, offset: 0 }));
    stubApi({ getQuizzes });

    render(<HistorySection pageSize={2} />);
    await screen.findByTestId("history-list-quizzes");

    fireEvent.click(screen.getByLabelText("Next page of quiz history"));
    await waitFor(() =>
      expect(within(quizList()).getByText("Quiz 2")).toBeInTheDocument(),
    );
    expect(getQuizzes).toHaveBeenLastCalledWith({
      subjectId: undefined,
      limit: 2,
      offset: 2,
    });
    expect(screen.getByTestId("Quiz history-range")).toHaveTextContent("Showing 3–3 of 5");

    fireEvent.click(screen.getByLabelText("Previous page of quiz history"));
    await waitFor(() => expect(within(quizList()).getByText("Quiz 0")).toBeInTheDocument());
    expect(getQuizzes).toHaveBeenLastCalledWith({
      subjectId: undefined,
      limit: 2,
      offset: 0,
    });
  });

  it("never asks for a page past the end", async () => {
    const getQuizzes = vi
      .fn()
      .mockResolvedValue(
        makePage([makeQuizSummary()], { total: 1, limit: 20, offset: 0 }),
      );
    stubApi({ getQuizzes });

    render(<HistorySection />);
    await screen.findByTestId("history-list-quizzes");

    expect(screen.getByLabelText("Next page of quiz history")).toBeDisabled();
    expect(screen.getByTestId("Quiz history-range")).toHaveTextContent("Showing 1–1 of 1");
    expect(getQuizzes).toHaveBeenCalledTimes(1);
  });

  it("passes the page size and subject scope through to the API", async () => {
    render(<HistorySection subjectId="subject-42" pageSize={5} timelineLimit={3} />);

    await waitFor(() =>
      expect(api.getAnalyticsOverview).toHaveBeenCalledWith({
        subjectId: "subject-42",
        limit: 3,
      }),
    );
    expect(api.getQuizzes).toHaveBeenCalledWith({
      subjectId: "subject-42",
      limit: 5,
      offset: 0,
    });
  });

  it("surfaces a history list error", async () => {
    stubApi({
      getQuizzes: vi.fn().mockRejectedValue(new Error("History unavailable.")),
    });

    render(<HistorySection />);

    expect(await screen.findByText("History unavailable.")).toBeInTheDocument();
  });

  it("lists unattempted quizzes without a misleading score", async () => {
    stubApi({
      getQuizzes: vi.fn().mockResolvedValue(
        makePage([
          makeQuizSummary({ id: "fresh", title: "Never Attempted", attempt_count: 0 }),
        ]),
      ),
    });

    render(<HistorySection />);

    await screen.findByTestId("history-list-quizzes");
    expect(within(quizList()).getByText("Never Attempted")).toBeInTheDocument();
    expect(screen.getByText("Not attempted")).toBeInTheDocument();
    expect(screen.queryByText("0%")).not.toBeInTheDocument();
  });

  it("reports the subject rollup and offers navigation when a handler is given", async () => {
    const onNavigateToSubject = vi.fn();
    render(<HistorySection onNavigateToSubject={onNavigateToSubject} />);

    await waitFor(() => expect(screen.getByText("Subjects in progress")).toBeInTheDocument());
    fireEvent.click(screen.getByText("Organic Chemistry"));

    expect(onNavigateToSubject).toHaveBeenCalledWith(
      "11111111-1111-4111-8111-111111111111",
    );
  });

  it("renders the weakest topics reused from mastery", async () => {
    render(<HistorySection />);

    expect(await screen.findByText("Topics to revisit")).toBeInTheDocument();
    expect(screen.getByText("Nucleophilic substitution")).toBeInTheDocument();
  });
});
