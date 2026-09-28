import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { makeNextLearning, makeNextLearningRecommendation } from "./factories";

const push = vi.fn();

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: (...args: unknown[]) => push(...args) }),
}));

vi.mock("@/lib/api", () => ({
  api: { getNextLearning: vi.fn() },
}));

import { api } from "@/lib/api";
import { NextBestAction } from "@/components/dashboard/NextBestAction";

const getNextLearning = vi.mocked(api.getNextLearning);

beforeEach(() => {
  push.mockReset();
  getNextLearning.mockReset();
});

describe("NextBestAction", () => {
  it("shows a loading state before the first response arrives", () => {
    getNextLearning.mockReturnValue(new Promise(() => {}));

    render(<NextBestAction />);

    expect(screen.getByText("Choosing your next best step...")).toBeInTheDocument();
  });

  it("requests the next learning recommendation on mount", async () => {
    getNextLearning.mockResolvedValue(makeNextLearning());

    render(<NextBestAction />);

    await waitFor(() => expect(getNextLearning).toHaveBeenCalledTimes(1));
  });

  it("prompts the user when there is not enough data yet", async () => {
    getNextLearning.mockResolvedValue(makeNextLearning({ status: "insufficient_data", recommendation: null }));

    render(<NextBestAction />);

    expect(await screen.findByText("No next step yet")).toBeInTheDocument();
  });

  it("surfaces the API error message when loading fails", async () => {
    getNextLearning.mockRejectedValue(new Error("Subject not found."));

    render(<NextBestAction />);

    expect(await screen.findByText("Subject not found.")).toBeInTheDocument();
  });

  it("falls back to a generic message when the error has none", async () => {
    getNextLearning.mockRejectedValue({});

    render(<NextBestAction />);

    expect(await screen.findByText("Failed to load your next best action.")).toBeInTheDocument();
  });

  it("retries after an error and renders the recommendation", async () => {
    getNextLearning
      .mockRejectedValueOnce(new Error("Temporary failure."))
      .mockResolvedValueOnce(makeNextLearning({ status: "insufficient_data", recommendation: null }));

    render(<NextBestAction />);

    fireEvent.click(await screen.findByText("Retry"));

    expect(await screen.findByText("No next step yet")).toBeInTheDocument();
    expect(getNextLearning).toHaveBeenCalledTimes(2);
  });

  it("renders the topic, subject, metrics, reason, and activity badge", async () => {
    getNextLearning.mockResolvedValue(
      makeNextLearning({
        recommendation: makeNextLearningRecommendation({
          topic: "Nucleophilic substitution",
          subject_title: "Organic Chemistry",
          mastery: 31,
          accuracy: 40,
          priority: 82,
          activity_type: "quiz",
          reason: "Low mastery with 6 missed questions.",
        }),
      }),
    );

    render(<NextBestAction />);

    expect(await screen.findByText("Nucleophilic substitution")).toBeInTheDocument();
    expect(screen.getByText("Organic Chemistry")).toBeInTheDocument();
    expect(screen.getByText("Mastery 31% • Accuracy 40%")).toBeInTheDocument();
    expect(screen.getByText("Priority 82")).toBeInTheDocument();
    expect(screen.getByText("Low mastery with 6 missed questions.")).toBeInTheDocument();
    expect(screen.getByText("Quiz")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Take a quiz/ })).toBeInTheDocument();
  });

  it.each([
    ["study", "Study materials", "materials"],
    ["quiz", "Take a quiz", "quiz-engine"],
    ["flashcards", "Review flashcards", "flashcards"],
  ] as const)(
    "deep-links a %s activity into the matching subject section",
    async (activity_type, cta, focus) => {
      getNextLearning.mockResolvedValue(
        makeNextLearning({
          recommendation: makeNextLearningRecommendation({
            subject_id: "subject-1",
            topic: "Vector databases",
            activity_type,
          }),
        }),
      );

      render(<NextBestAction />);

      fireEvent.click(await screen.findByRole("button", { name: cta }));

      expect(push).toHaveBeenCalledWith(
        `/subjects/subject-1?topic=Vector%20databases&focus=${focus}`,
      );
    },
  );

  it("shows the number of due cards for a flashcard recommendation", async () => {
    getNextLearning.mockResolvedValue(
      makeNextLearning({
        recommendation: makeNextLearningRecommendation({
          topic: "Vector databases",
          activity_type: "flashcards",
          due_cards: 3,
        }),
      }),
    );

    render(<NextBestAction />);

    expect(await screen.findByText("3 cards due")).toBeInTheDocument();
  });

  it("refetches when refreshKey changes", async () => {
    getNextLearning.mockResolvedValue(makeNextLearning());

    const { rerender } = render(<NextBestAction refreshKey={0} />);
    await waitFor(() => expect(getNextLearning).toHaveBeenCalledTimes(1));

    rerender(<NextBestAction refreshKey={1} />);
    await waitFor(() => expect(getNextLearning).toHaveBeenCalledTimes(2));
  });
});