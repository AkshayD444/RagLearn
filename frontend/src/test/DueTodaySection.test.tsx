import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  makeFlashcard,
  makeFlashcardDeck,
  makeFlashcardReviewSummary,
  makeFlashcardSchedule,
} from "./factories";

vi.mock("@/lib/api", () => ({
  api: {
    getDueFlashcards: vi.fn(),
    getFlashcardDeck: vi.fn(),
    reviewFlashcardDeck: vi.fn(),
    rescheduleFlashcard: vi.fn(),
  },
}));

import { DueTodaySection } from "@/components/dashboard/DueTodaySection";
import { api } from "@/lib/api";

const getDueFlashcards = vi.mocked(api.getDueFlashcards);
const getFlashcardDeck = vi.mocked(api.getFlashcardDeck);
const reviewFlashcardDeck = vi.mocked(api.reviewFlashcardDeck);
const rescheduleFlashcard = vi.mocked(api.rescheduleFlashcard);

const CARD_A = "55555555-5555-4555-8555-555555555555";
const CARD_B = "88888888-8888-4888-8888-888888888888";
const DECK_ID = "77777777-7777-4777-8777-777777777777";

const dueCardA = makeFlashcard({
  id: CARD_A,
  front_text: "What is HNSW indexing?",
  deck_id: DECK_ID,
  deck_title: "Flashcards: Vector Databases",
  schedule: makeFlashcardSchedule({ is_new: true, due_at: null, interval_days: 0 }),
});

const dueCardB = makeFlashcard({
  id: CARD_B,
  front_text: "What is IVF indexing?",
  deck_id: DECK_ID,
  deck_title: "Flashcards: Vector Databases",
  schedule: makeFlashcardSchedule({ is_new: false, due_at: "2026-01-01T00:00:00Z" }),
});

function queue(overrides: Partial<Awaited<ReturnType<typeof getDueFlashcards>>> = {}) {
  return {
    cards: [dueCardA, dueCardB],
    due_count: 2,
    new_count: 1,
    ...overrides,
  };
}

beforeEach(() => {
  getDueFlashcards.mockReset();
  getFlashcardDeck.mockReset();
  reviewFlashcardDeck.mockReset();
  rescheduleFlashcard.mockReset();
  getDueFlashcards.mockResolvedValue(queue() as any);
  rescheduleFlashcard.mockResolvedValue({
    card_id: CARD_A,
    schedule: makeFlashcardSchedule(),
  } as any);
});

describe("DueTodaySection queue", () => {
  it("reports the true queue size even when the page is shorter", async () => {
    getDueFlashcards.mockResolvedValue(
      queue({ cards: [dueCardA], due_count: 12, new_count: 5 }) as any,
    );

    render(<DueTodaySection />);

    expect(await screen.findByText("12 due")).toBeInTheDocument();
    // The count is the whole queue; the page is only what is displayed.
    expect(screen.getByText(/12 cards ready/)).toBeInTheDocument();
    expect(screen.getByText(/5 never studied/)).toBeInTheDocument();
    expect(screen.getByText(/showing 1/)).toBeInTheDocument();
  });

  it("says so plainly when nothing is due", async () => {
    getDueFlashcards.mockResolvedValue(queue({ cards: [], due_count: 0, new_count: 0 }) as any);

    render(<DueTodaySection />);

    expect(await screen.findByText(/Nothing due right now/)).toBeInTheDocument();
  });

  it("groups the queue by deck", async () => {
    getDueFlashcards.mockResolvedValue(
      queue({
        cards: [
          dueCardA,
          dueCardB,
          makeFlashcard({
            id: "99999999-9999-4999-8999-999999999999",
            front_text: "What is a graph index?",
            deck_id: "44444444-4444-4444-8444-444444444444",
            deck_title: "Flashcards: Graph Stores",
          }),
        ],
        due_count: 3,
      }) as any,
    );

    render(<DueTodaySection />);

    expect(await screen.findByText("Flashcards: Vector Databases")).toBeInTheDocument();
    expect(screen.getByText("Flashcards: Graph Stores")).toBeInTheDocument();
    // Grouped, not flattened: one Study action per deck.
    expect(screen.getAllByRole("button", { name: /Study/ })).toHaveLength(2);
  });

  it("scopes the request to a subject when one is given", async () => {
    render(<DueTodaySection subjectId="11111111-1111-4111-8111-111111111111" />);

    await waitFor(() =>
      expect(getDueFlashcards).toHaveBeenCalledWith({
        subjectId: "11111111-1111-4111-8111-111111111111",
        limit: 20,
      }),
    );
  });

  it("keeps the error banner when the queue cannot be loaded", async () => {
    getDueFlashcards.mockRejectedValue(new Error("Network down"));

    render(<DueTodaySection />);

    expect(await screen.findByText("Network down")).toBeInTheDocument();
  });
});

describe("DueTodaySection review flow", () => {
  it("studies the whole deck with the due cards first", async () => {
    // A reviewed card that is not due yet: it still has to be in the session,
    // because the review endpoint grades the deck as a whole.
    getFlashcardDeck.mockResolvedValue(
      makeFlashcardDeck({
        id: DECK_ID,
        cards: [
          makeFlashcard({ id: "aaaa", front_text: "Not due yet", position: 0, deck_id: DECK_ID }),
          makeFlashcard({ id: CARD_A, front_text: "What is HNSW indexing?", position: 1, deck_id: DECK_ID }),
          makeFlashcard({ id: CARD_B, front_text: "What is IVF indexing?", position: 2, deck_id: DECK_ID }),
        ],
      }) as any,
    );
    reviewFlashcardDeck.mockResolvedValue(
      makeFlashcardReviewSummary({ due_count: 0, new_count: 0 }) as any,
    );

    render(<DueTodaySection />);
    fireEvent.click(await screen.findByRole("button", { name: /Study/ }));

    expect(await screen.findByText(/Card 1 of 3/)).toBeInTheDocument();
    // The due card leads, not the deck's own position order.
    expect(screen.getByText("What is HNSW indexing?")).toBeInTheDocument();
    expect(screen.getByText(/not-yet-due cards are in this session/)).toBeInTheDocument();
  });

  it("submits a full-deck review and reports the new intervals", async () => {
    getFlashcardDeck.mockResolvedValue(
      makeFlashcardDeck({ id: DECK_ID, cards: [dueCardA, dueCardB] }) as any,
    );
    reviewFlashcardDeck.mockResolvedValue(
      makeFlashcardReviewSummary({
        reviewed_count: 1,
        correct_count: 1,
        percentage: 100,
        due_count: 0,
        new_count: 0,
        results: [
          {
            card_id: CARD_A,
            is_correct: true,
            response: null,
            review_count: 1,
            correct_count: 1,
            schedule: makeFlashcardSchedule({ interval_days: 6 }),
          },
        ],
      }) as any,
    );

    render(<DueTodaySection />);
    fireEvent.click(await screen.findByRole("button", { name: /Study/ }));
    await screen.findByText(/Card 1 of 2/);

    fireEvent.click(screen.getByRole("button", { name: /Reveal answer/ }));
    fireEvent.click(screen.getByRole("button", { name: /I knew it/ }));
    await screen.findByText(/Card 2 of 2/);
    fireEvent.click(screen.getByRole("button", { name: /Reveal answer/ }));
    fireEvent.click(screen.getByRole("button", { name: /Still learning/ }));
    fireEvent.click(screen.getByRole("button", { name: /Finish review/ }));

    await waitFor(() =>
      expect(reviewFlashcardDeck).toHaveBeenCalledWith(DECK_ID, [
        { card_id: CARD_A, is_correct: true },
        { card_id: CARD_B, is_correct: false },
      ]),
    );
    expect(await screen.findByText(/Review saved/)).toBeInTheDocument();
  });

  it("tells the parent so its own counts can refresh", async () => {
    const onReviewComplete = vi.fn();
    getFlashcardDeck.mockResolvedValue(
      makeFlashcardDeck({ id: DECK_ID, cards: [dueCardA, dueCardB] }) as any,
    );
    reviewFlashcardDeck.mockResolvedValue(makeFlashcardReviewSummary() as any);

    render(<DueTodaySection onReviewComplete={onReviewComplete} />);
    fireEvent.click(await screen.findByRole("button", { name: /Study/ }));
    await screen.findByText(/Card 1 of 2/);

    fireEvent.click(screen.getByRole("button", { name: /Reveal answer/ }));
    fireEvent.click(screen.getByRole("button", { name: /I knew it/ }));
    await screen.findByText(/Card 2 of 2/);
    fireEvent.click(screen.getByRole("button", { name: /Reveal answer/ }));
    fireEvent.click(screen.getByRole("button", { name: /I knew it/ }));
    fireEvent.click(screen.getByRole("button", { name: /Finish review/ }));

    await waitFor(() => expect(onReviewComplete).toHaveBeenCalled());
  });

  it("cannot finish a review with nothing graded", async () => {
    getFlashcardDeck.mockResolvedValue(
      makeFlashcardDeck({ id: DECK_ID, cards: [dueCardA, dueCardB] }) as any,
    );

    render(<DueTodaySection />);
    fireEvent.click(await screen.findByRole("button", { name: /Study/ }));
    await screen.findByText(/Card 1 of 2/);
    fireEvent.click(screen.getByRole("button", { name: /Reveal answer/ }));
    fireEvent.click(screen.getByRole("button", { name: /Next/ }));
    fireEvent.click(screen.getByRole("button", { name: /Reveal answer/ }));

    expect(screen.getByRole("button", { name: /Finish review/ })).toBeDisabled();
  });
});

describe("DueTodaySection reschedule", () => {
  it("returns a card to the queue and reloads the counts", async () => {
    render(<DueTodaySection />);
    await screen.findByText("Flashcards: Vector Databases");

    fireEvent.click(screen.getAllByRole("button", { name: /Reschedule/ })[0]);

    await waitFor(() => expect(rescheduleFlashcard).toHaveBeenCalledWith(CARD_A));
    await waitFor(() => expect(getDueFlashcards).toHaveBeenCalledTimes(2));
  });

  it("surfaces a rejected reschedule instead of dropping the card", async () => {
    rescheduleFlashcard.mockRejectedValue(new Error("Card not found"));

    render(<DueTodaySection />);
    await screen.findByText("Flashcards: Vector Databases");

    fireEvent.click(screen.getAllByRole("button", { name: /Reschedule/ })[0]);

    expect(await screen.findByText("Card not found")).toBeInTheDocument();
  });
});
