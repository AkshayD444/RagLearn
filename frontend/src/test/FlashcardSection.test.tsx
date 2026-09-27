import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  makeFlashcardDeck,
  makeFlashcardDeckSummary,
  makeFlashcardReviewSummary,
  makeFlashcardSchedule,
  makeFlashcardTopicProgress,
} from "./factories";

vi.mock("@/lib/api", () => ({
  api: {
    getFlashcardDecks: vi.fn(),
    getFlashcardProgress: vi.fn(),
    getFlashcardDeck: vi.fn(),
    generateFlashcards: vi.fn(),
    deleteFlashcardDeck: vi.fn(),
    reviewFlashcardDeck: vi.fn(),
  },
}));

import { api } from "@/lib/api";
import { FlashcardSection } from "@/components/flashcards/FlashcardSection";

const getFlashcardDecks = vi.mocked(api.getFlashcardDecks);
const getFlashcardProgress = vi.mocked(api.getFlashcardProgress);
const getFlashcardDeck = vi.mocked(api.getFlashcardDeck);
const generateFlashcards = vi.mocked(api.generateFlashcards);
const deleteFlashcardDeck = vi.mocked(api.deleteFlashcardDeck);
const reviewFlashcardDeck = vi.mocked(api.reviewFlashcardDeck);

// Must match the id in the flashcard factories.
const DECK_ID = "77777777-7777-4777-8777-777777777777";

beforeEach(() => {
  getFlashcardDecks.mockReset().mockResolvedValue([]);
  getFlashcardProgress.mockReset().mockResolvedValue([]);
  getFlashcardDeck.mockReset();
  generateFlashcards.mockReset();
  deleteFlashcardDeck.mockReset().mockResolvedValue(undefined);
  reviewFlashcardDeck.mockReset();
});

async function renderSection(props: Partial<React.ComponentProps<typeof FlashcardSection>> = {}) {
  const view = render(
    <FlashcardSection subjectId="subject-1" hasReadyDocuments {...props} />,
  );
  // Let the initial deck + progress loads settle.
  await waitFor(() => expect(getFlashcardDecks).toHaveBeenCalled());
  return view;
}

describe("FlashcardSection generation", () => {
  it("blocks generation until a document is indexed", async () => {
    await renderSection({ hasReadyDocuments: false });

    expect(
      screen.getByText("Upload and process a document first — flashcards are generated from your material."),
    ).toBeInTheDocument();
    const generateButton = screen.getByRole("button", { name: /Generate flashcards/ });
    expect(generateButton).toBeDisabled();
    expect(generateFlashcards).not.toHaveBeenCalled();
  });

  it("prefills the topic from initialTopic and uses it when generating", async () => {
    generateFlashcards.mockResolvedValue(makeFlashcardDeck());
    await renderSection({ initialTopic: "Vector Databases" });

    expect(screen.getByLabelText(/Topic/)).toHaveValue("Vector Databases");

    fireEvent.click(screen.getByRole("button", { name: /Generate flashcards/ }));

    await waitFor(() =>
      expect(generateFlashcards).toHaveBeenCalledWith("subject-1", 10, "Vector Databases"),
    );
  });

  it("passes the chosen card count and a null topic when the field is cleared", async () => {
    generateFlashcards.mockResolvedValue(makeFlashcardDeck());
    await renderSection({ initialTopic: "Vector Databases" });

    fireEvent.click(screen.getByRole("button", { name: "20" }));
    fireEvent.change(screen.getByLabelText(/Topic/), { target: { value: "   " } });
    fireEvent.click(screen.getByRole("button", { name: /Generate flashcards/ }));

    await waitFor(() => expect(generateFlashcards).toHaveBeenCalledWith("subject-1", 20, null));
  });

  it("surfaces a backend rejection instead of silently failing", async () => {
    generateFlashcards.mockRejectedValue(new Error("No processed documents found for this subject."));
    await renderSection();

    fireEvent.click(screen.getByRole("button", { name: /Generate flashcards/ }));

    expect(
      await screen.findByText("No processed documents found for this subject."),
    ).toBeInTheDocument();
  });
});

describe("FlashcardSection deck browsing", () => {
  it("shows an empty state when the subject has no decks", async () => {
    await renderSection();

    expect(screen.getByText("No decks yet")).toBeInTheDocument();
  });

  it("lists an existing deck with its review progress", async () => {
    getFlashcardDecks.mockResolvedValue([
      makeFlashcardDeckSummary({
        card_count: 4,
        reviewed_card_count: 2,
        correct_count: 1,
      }),
    ]);

    await renderSection();

    expect(await screen.findByText("Flashcards: Vector Databases")).toBeInTheDocument();
    expect(screen.getByText("4 cards • 2 reviewed • 1 correct")).toBeInTheDocument();
  });

  it("deletes a deck and refreshes the list", async () => {
    const decks = [makeFlashcardDeckSummary()];
    getFlashcardDecks.mockResolvedValue(decks);
    deleteFlashcardDeck.mockImplementation(async () => {
      getFlashcardDecks.mockResolvedValue([]);
      return undefined;
    });

    await renderSection();
    fireEvent.click(await screen.findByLabelText("Delete Flashcards: Vector Databases"));

    await waitFor(() => expect(deleteFlashcardDeck).toHaveBeenCalledWith(DECK_ID));
    expect(await screen.findByText("No decks yet")).toBeInTheDocument();
  });
});

describe("FlashcardSection review flow", () => {
  async function startGeneratedReview() {
    generateFlashcards.mockResolvedValue(makeFlashcardDeck());
    await renderSection();
    fireEvent.click(screen.getByRole("button", { name: /Generate flashcards/ }));
    await screen.findByText(/Card 1 of 2/);
  }

  it("keeps the answer hidden until the learner reveals it", async () => {
    await startGeneratedReview();

    const answer = "High-dimensional embeddings, so similarity search stays fast.";
    expect(screen.queryByText(answer)).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /Reveal answer/ }));

    expect(screen.getByText(answer)).toBeInTheDocument();
  });

  it("shows the citation snippet and source document for the revealed card", async () => {
    await startGeneratedReview();
    fireEvent.click(screen.getByRole("button", { name: /Reveal answer/ }));

    expect(
      screen.getByText("Vector databases store high-dimensional embeddings."),
    ).toBeInTheDocument();
    expect(screen.getByText(/lecture-01.pdf, p\. 3/)).toBeInTheDocument();
  });

  it("reveals the hint only on request when a card has one", async () => {
    await startGeneratedReview();

    expect(screen.queryByText("Think about embeddings.")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Show hint/ }));
    expect(screen.getByText("Think about embeddings.")).toBeInTheDocument();
  });

  it("advances after self-grading and only submits the graded cards", async () => {
    reviewFlashcardDeck.mockResolvedValue(
      makeFlashcardReviewSummary({
        reviewed_count: 2,
        correct_count: 1,
        incorrect_count: 1,
        percentage: 50,
        results: [
          { card_id: "55555555-5555-4555-8555-555555555555", is_correct: true, response: null, review_count: 1, correct_count: 1, schedule: makeFlashcardSchedule() },
          { card_id: "88888888-8888-4888-8888-888888888888", is_correct: false, response: null, review_count: 1, correct_count: 0, schedule: makeFlashcardSchedule() },
        ],
      }),
    );

    await startGeneratedReview();

    fireEvent.click(screen.getByRole("button", { name: /Reveal answer/ }));
    fireEvent.click(screen.getByRole("button", { name: /I knew it/ }));
    await screen.findByText(/Card 2 of 2/);

    fireEvent.click(screen.getByRole("button", { name: /Reveal answer/ }));
    fireEvent.click(screen.getByRole("button", { name: /Still learning/ }));
    fireEvent.click(screen.getByRole("button", { name: /Finish review/ }));

    await waitFor(() =>
      expect(reviewFlashcardDeck).toHaveBeenCalledWith(DECK_ID, [
        { card_id: "55555555-5555-4555-8555-555555555555", is_correct: true },
        { card_id: "88888888-8888-4888-8888-888888888888", is_correct: false },
      ]),
    );
  });

  it("shows the accuracy summary and the cards still to revisit", async () => {
    reviewFlashcardDeck.mockResolvedValue(
      makeFlashcardReviewSummary({
        reviewed_count: 2,
        correct_count: 1,
        incorrect_count: 1,
        percentage: 50,
        results: [
          { card_id: "55555555-5555-4555-8555-555555555555", is_correct: true, response: null, review_count: 1, correct_count: 1, schedule: makeFlashcardSchedule() },
          { card_id: "88888888-8888-4888-8888-888888888888", is_correct: false, response: null, review_count: 1, correct_count: 0, schedule: makeFlashcardSchedule() },
        ],
      }),
    );

    await startGeneratedReview();
    fireEvent.click(screen.getByRole("button", { name: /Reveal answer/ }));
    fireEvent.click(screen.getByRole("button", { name: /I knew it/ }));
    await screen.findByText(/Card 2 of 2/);
    fireEvent.click(screen.getByRole("button", { name: /Reveal answer/ }));
    fireEvent.click(screen.getByRole("button", { name: /Still learning/ }));
    fireEvent.click(screen.getByRole("button", { name: /Finish review/ }));

    expect(await screen.findByText("50%")).toBeInTheDocument();
    expect(screen.getByText("1 correct • 1 to revisit")).toBeInTheDocument();
    // Only the missed card is repeated for revision.
    expect(screen.getByText("What is retrieval augmented generation?")).toBeInTheDocument();
    expect(
      screen.queryByText("What does a vector database index?"),
    ).not.toBeInTheDocument();
  });

  it("notifies the parent so sibling sections can refresh", async () => {
    const onReviewComplete = vi.fn();
    reviewFlashcardDeck.mockResolvedValue(
      makeFlashcardReviewSummary({
        reviewed_count: 1,
        correct_count: 1,
        incorrect_count: 0,
        percentage: 100,
        results: [
          { card_id: "55555555-5555-4555-8555-555555555555", is_correct: true, response: null, review_count: 1, correct_count: 1, schedule: makeFlashcardSchedule() },
        ],
      }),
    );

    generateFlashcards.mockResolvedValue(
      makeFlashcardDeck({ cards: [makeFlashcardDeck().cards[0]] }),
    );
    await renderSection({ onReviewComplete });

    fireEvent.click(screen.getByRole("button", { name: /Generate flashcards/ }));
    await screen.findByText(/Card 1 of 1/);
    fireEvent.click(screen.getByRole("button", { name: /Reveal answer/ }));
    fireEvent.click(screen.getByRole("button", { name: /I knew it/ }));
    fireEvent.click(screen.getByRole("button", { name: /Finish review/ }));

    await waitFor(() => expect(onReviewComplete).toHaveBeenCalled());
  });

  it("cannot finish a review with nothing graded", async () => {
    await startGeneratedReview();

    // Skipping forward without grading leaves the last card ungraded.
    fireEvent.click(screen.getByRole("button", { name: /Next/ }));
    await screen.findByText(/Card 2 of 2/);

    const finishButton = screen.getByRole("button", { name: /Finish review/ });
    expect(finishButton).toBeDisabled();
    expect(reviewFlashcardDeck).not.toHaveBeenCalled();
  });

  it("keeps the error banner when saving the review fails", async () => {
    reviewFlashcardDeck.mockRejectedValue(new Error("Review save failed."));
    await startGeneratedReview();

    fireEvent.click(screen.getByRole("button", { name: /Reveal answer/ }));
    fireEvent.click(screen.getByRole("button", { name: /I knew it/ }));
    await screen.findByText(/Card 2 of 2/);
    fireEvent.click(screen.getByRole("button", { name: /Reveal answer/ }));
    fireEvent.click(screen.getByRole("button", { name: /I knew it/ }));
    fireEvent.click(screen.getByRole("button", { name: /Finish review/ }));

    expect(await screen.findByText("Review save failed.")).toBeInTheDocument();
  });

  it("reopens a saved deck from the browse list", async () => {
    getFlashcardDecks.mockResolvedValue([makeFlashcardDeckSummary()]);
    getFlashcardDeck.mockResolvedValue(makeFlashcardDeck());

    await renderSection();
    fireEvent.click(await screen.findByRole("button", { name: /Review/ }));

    await waitFor(() => expect(getFlashcardDeck).toHaveBeenCalledWith(DECK_ID));
    expect(await screen.findByText(/Card 1 of 2/)).toBeInTheDocument();
  });
});

describe("FlashcardSection topic progress", () => {
  it("shows mastery as read-only alongside per-topic accuracy", async () => {
    getFlashcardProgress.mockResolvedValue([
      makeFlashcardTopicProgress({
        card_count: 4,
        reviewed_count: 2,
        correct_count: 1,
        accuracy: 50,
        mastery_level: "developing",
      }),
    ]);

    await renderSection();

    expect(await screen.findByText("Progress by topic")).toBeInTheDocument();
    expect(screen.getByText("Vector Databases")).toBeInTheDocument();
    expect(screen.getByText("2/4 • 50%")).toBeInTheDocument();
    expect(screen.getByText("developing")).toBeInTheDocument();
    expect(
      screen.getByText("Mastery comes from your quiz history; reviewing flashcards never changes it."),
    ).toBeInTheDocument();
  });

  it("hides the progress panel when nothing has been generated yet", async () => {
    await renderSection();

    await waitFor(() => expect(getFlashcardProgress).toHaveBeenCalled());
    expect(screen.queryByText("Progress by topic")).not.toBeInTheDocument();
  });
});
