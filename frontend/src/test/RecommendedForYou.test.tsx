import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { makeRecommendation } from "./factories";

const push = vi.fn();

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: (...args: unknown[]) => push(...args) }),
}));

vi.mock("@/lib/api", () => ({
  api: { getTopRecommendations: vi.fn() },
}));

import { api } from "@/lib/api";
import { RecommendedForYou } from "@/components/recommendations/RecommendedForYou";

const getTopRecommendations = vi.mocked(api.getTopRecommendations);

beforeEach(() => {
  push.mockReset();
  getTopRecommendations.mockReset();
});

describe("RecommendedForYou", () => {
  it("shows a loading state before the first response arrives", () => {
    getTopRecommendations.mockReturnValue(new Promise(() => {}));

    render(<RecommendedForYou />);

    expect(screen.getByText("Computing adaptive recommendations...")).toBeInTheDocument();
  });

  it("requests the caller's limit", async () => {
    getTopRecommendations.mockResolvedValue([]);

    render(<RecommendedForYou limit={3} />);

    await waitFor(() => expect(getTopRecommendations).toHaveBeenCalledWith(3));
  });

  it("defaults the limit to 5", async () => {
    getTopRecommendations.mockResolvedValue([]);

    render(<RecommendedForYou />);

    await waitFor(() => expect(getTopRecommendations).toHaveBeenCalledWith(5));
  });

  it("prompts the user to take a quiz when there is no data yet", async () => {
    getTopRecommendations.mockResolvedValue([]);

    render(<RecommendedForYou />);

    expect(await screen.findByText("No recommendations yet")).toBeInTheDocument();
  });

  it("surfaces the API error message when loading fails", async () => {
    getTopRecommendations.mockRejectedValue(new Error("Subject not found."));

    render(<RecommendedForYou />);

    expect(await screen.findByText("Subject not found.")).toBeInTheDocument();
  });

  it("falls back to a generic message when the error has none", async () => {
    getTopRecommendations.mockRejectedValue({});

    render(<RecommendedForYou />);

    expect(await screen.findByText("Failed to load recommendations.")).toBeInTheDocument();
  });

  it("renders the topic, subject, metrics, type, and reason for each card", async () => {
    getTopRecommendations.mockResolvedValue([
      makeRecommendation({
        topic: "Nucleophilic substitution",
        subject_title: "Organic Chemistry",
        mastery: 31,
        accuracy: 40,
        questions_attempted: 10,
        questions_incorrect: 6,
        recommendation_type: "REVIEW",
        priority: 82,
        reason: "Low mastery with 6 missed questions.",
      }),
    ]);

    render(<RecommendedForYou />);

    expect(await screen.findByText("Nucleophilic substitution")).toBeInTheDocument();
    expect(screen.getByText("Organic Chemistry")).toBeInTheDocument();
    expect(screen.getByText("Mastery 31% • Accuracy 40%")).toBeInTheDocument();
    expect(screen.getByText("10 questions • 6 missed")).toBeInTheDocument();
    expect(screen.getByText("REVIEW")).toBeInTheDocument();
    expect(screen.getByText("Priority 82")).toBeInTheDocument();
    expect(screen.getByText("Low mastery with 6 missed questions.")).toBeInTheDocument();
  });

  it.each(["REVIEW", "PRACTICE", "MAINTAIN"] as const)(
    "renders the %s badge",
    async (recommendation_type) => {
      getTopRecommendations.mockResolvedValue([
        makeRecommendation({ topic: `Topic ${recommendation_type}`, recommendation_type }),
      ]);

      render(<RecommendedForYou />);

      expect(await screen.findByText(recommendation_type)).toBeInTheDocument();
    },
  );

  it("caps the priority bar width at 100%", async () => {
    getTopRecommendations.mockResolvedValue([
      makeRecommendation({ topic: "Overflowing", priority: 140 }),
    ]);

    const { container } = render(<RecommendedForYou />);
    await screen.findByText("Overflowing");

    const bar = container.querySelector("div[style]") as HTMLElement;
    expect(bar.style.width).toBe("100%");
  });

  it("deep-links Practice into the subject page with the topic prefill", async () => {
    getTopRecommendations.mockResolvedValue([
      makeRecommendation({
        subject_id: "subject-1",
        topic: "Nucleophilic substitution",
        suggested_action: "Review the source material, then retry.",
      }),
    ]);

    render(<RecommendedForYou />);

    const button = await screen.findByTitle("Review the source material, then retry.");
    fireEvent.click(button);

    expect(push).toHaveBeenCalledWith(
      "/subjects/subject-1?topic=Nucleophilic%20substitution",
    );
  });

  it("URL-encodes topics that contain query characters", async () => {
    getTopRecommendations.mockResolvedValue([
      makeRecommendation({ subject_id: "subject-1", topic: "A & B?x=1" }),
    ]);

    render(<RecommendedForYou />);

    fireEvent.click(await screen.findByRole("button", { name: /Practice/ }));

    expect(push).toHaveBeenCalledWith("/subjects/subject-1?topic=A%20%26%20B%3Fx%3D1");
  });

  it("refetches when refreshKey changes", async () => {
    getTopRecommendations.mockResolvedValue([]);

    const { rerender } = render(<RecommendedForYou refreshKey={0} />);
    await waitFor(() => expect(getTopRecommendations).toHaveBeenCalledTimes(1));

    rerender(<RecommendedForYou refreshKey={1} />);
    await waitFor(() => expect(getTopRecommendations).toHaveBeenCalledTimes(2));
  });
});
