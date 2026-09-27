"use client";

import React, { useCallback, useEffect, useMemo, useState } from "react";
import { api, Flashcard } from "@/lib/api";
import { CalendarClock, RotateCcw, Layers, AlertCircle } from "lucide-react";
import { Spinner } from "@/components/shared/Spinner";
import { ErrorBanner } from "@/components/shared/ErrorBanner";
import { CardStudySession, describeInterval } from "@/components/flashcards/CardStudySession";

interface DueTodaySectionProps {
  /** Scopes the queue to one subject; omit for a cross-subject dashboard queue. */
  subjectId?: string;
  /** How many cards to pull for the preview. The counts ignore this limit. */
  limit?: number;
  /** Fired after a review is saved so the parent can refresh its own counts. */
  onReviewComplete?: () => void;
}

/** One deck's share of the queue. */
interface DueDeck {
  deckId: string;
  deckTitle: string;
  cards: Flashcard[];
}

/**
 * "Due today": what the review queue looks like right now, and a way into it.
 *
 * The queue spans decks, but a review is submitted per deck and the API
 * requires one result for *every* card in that deck. So this never presents a
 * cross-deck study session it cannot actually save: starting a deck fetches
 * that whole deck and puts the due cards first, which is the same set of cards
 * a deck review always covers.
 */
export function DueTodaySection({
  subjectId,
  limit = 20,
  onReviewComplete,
}: DueTodaySectionProps) {
  const [cards, setCards] = useState<Flashcard[]>([]);
  const [dueCount, setDueCount] = useState(0);
  const [newCount, setNewCount] = useState(0);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // The deck being studied, and its full card list, loaded on demand.
  const [activeDeckId, setActiveDeckId] = useState<string | null>(null);
  const [activeCards, setActiveCards] = useState<Flashcard[] | null>(null);
  const [activeTitle, setActiveTitle] = useState("");
  const [isOpening, setIsOpening] = useState<string | null>(null);

  const loadQueue = useCallback(async () => {
    try {
      const queue = await api.getDueFlashcards({ subjectId, limit });
      setCards(queue.cards);
      setDueCount(queue.due_count);
      setNewCount(queue.new_count);
    } catch (err: any) {
      setError(err.message || "Failed to load your review queue.");
    } finally {
      setIsLoading(false);
    }
  }, [subjectId, limit]);

  useEffect(() => {
    setIsLoading(true);
    loadQueue();
  }, [loadQueue]);

  const decks = useMemo<DueDeck[]>(() => {
    const grouped = new Map<string, DueDeck>();
    for (const card of cards) {
      const existing = grouped.get(card.deck_id);
      if (existing) {
        existing.cards.push(card);
      } else {
        grouped.set(card.deck_id, {
          deckId: card.deck_id,
          deckTitle: card.deck_title,
          cards: [card],
        });
      }
    }
    return Array.from(grouped.values()).sort((a, b) => b.cards.length - a.cards.length);
  }, [cards]);

  const openDeck = async (deckId: string) => {
    setError(null);
    setIsOpening(deckId);
    try {
      const deck = await api.getFlashcardDeck(deckId);
      const dueIds = new Set(cards.filter((c) => c.deck_id === deckId).map((c) => c.id));
      setActiveCards([
        // Due cards first so the session opens on what actually needs work.
        ...deck.cards.filter((c) => dueIds.has(c.id)),
        ...deck.cards.filter((c) => !dueIds.has(c.id)),
      ]);
      setActiveTitle(deck.title);
      setActiveDeckId(deckId);
    } catch (err: any) {
      setError(err.message || "Failed to open that deck.");
    } finally {
      setIsOpening(null);
    }
  };

  const closeDeck = () => {
    setActiveDeckId(null);
    setActiveCards(null);
    setActiveTitle("");
    loadQueue();
  };

  const reschedule = async (cardId: string) => {
    setError(null);
    try {
      await api.rescheduleFlashcard(cardId);
      await loadQueue();
    } catch (err: any) {
      setError(err.message || "Failed to reschedule that card.");
    }
  };

  if (activeDeckId && activeCards) {
    return (
      <CardStudySession
        cards={activeCards}
        title={activeTitle}
        onExit={closeDeck}
        footerNote={
          activeCards.length > (decks.find((d) => d.deckId === activeDeckId)?.cards.length ?? 0)
            ? "This deck is graded as a whole, so its not-yet-due cards are in this session too."
            : undefined
        }
        onSubmit={async (reviews) => {
          const result = await api.reviewFlashcardDeck(activeDeckId, reviews);
          onReviewComplete?.();
          return result;
        }}
      />
    );
  }

  return (
    <div className="space-y-4">
      {error && <ErrorBanner message={error} />}

      <div className="p-4 sm:p-5 rounded-2xl border border-slate-800 bg-slate-900/60 space-y-3">
        <div className="flex items-center justify-between gap-3">
          <h3 className="text-sm font-bold text-white flex items-center gap-2">
            <CalendarClock className="h-4 w-4 text-sky-400" /> Due today
          </h3>
          {dueCount > 0 && !isLoading && (
            <span className="inline-flex items-center px-2.5 py-1 rounded-full bg-sky-500/10 text-sky-300 text-[10px] font-semibold shrink-0">
              {dueCount} due
            </span>
          )}
        </div>

        {isLoading ? (
          <div className="flex items-center justify-center py-4">
            <Spinner />
          </div>
        ) : dueCount === 0 ? (
          <p className="text-[11px] text-slate-500">
            Nothing due right now. New cards show up here as soon as you generate a deck.
          </p>
        ) : (
          <p className="text-[11px] text-slate-500">
            {dueCount} card{dueCount === 1 ? "" : "s"} ready
            {newCount > 0 && ` • ${newCount} never studied`}
            {dueCount > cards.length && ` • showing ${cards.length}`}
          </p>
        )}
      </div>

      {!isLoading && decks.length > 0 && (
        <ul className="space-y-2.5">
          {decks.map((deck) => (
            <li
              key={deck.deckId}
              className="p-3.5 rounded-2xl border border-slate-800 bg-slate-900/60 space-y-3"
            >
              <div className="flex items-center justify-between gap-3">
                <div className="min-w-0 flex-1">
                  <p className="text-xs font-bold text-white truncate">{deck.deckTitle}</p>
                  <p className="text-[11px] text-slate-500">
                    {deck.cards.length} of this deck&rsquo;s cards ready
                  </p>
                </div>
                <button
                  onClick={() => openDeck(deck.deckId)}
                  disabled={isOpening === deck.deckId}
                  className="inline-flex items-center gap-1.5 px-3 py-2 rounded-xl bg-slate-800 text-white text-[11px] font-semibold hover:bg-slate-700 disabled:opacity-50 transition-colors shrink-0"
                >
                  {isOpening === deck.deckId ? (
                    <Spinner className="h-3.5 w-3.5" />
                  ) : (
                    <Layers className="h-3.5 w-3.5" />
                  )}
                  Study
                </button>
              </div>

              <ul className="space-y-1.5">
                {deck.cards.slice(0, 3).map((card) => (
                  <li
                    key={card.id}
                    className="flex items-center gap-2 rounded-xl border border-slate-800/70 bg-slate-950/40 px-2.5 py-2"
                  >
                    <div className="min-w-0 flex-1">
                      <p className="text-[11px] text-slate-300 truncate">{card.front_text}</p>
                      <p className="text-[10px] text-slate-600">
                        {card.schedule.is_new
                          ? "New"
                          : `Last interval ${describeInterval(card.schedule.interval_days)}`}
                      </p>
                    </div>
                    <button
                      onClick={() => reschedule(card.id)}
                      title="Bring this card back to the queue now"
                      className="inline-flex items-center gap-1 px-2 py-1 rounded-lg text-slate-500 hover:text-sky-400 hover:border-sky-800 shrink-0 transition-colors"
                    >
                      <RotateCcw className="h-3 w-3" />
                      <span className="text-[10px] font-semibold">Reschedule</span>
                    </button>
                  </li>
                ))}
                {deck.cards.length > 3 && (
                  <li className="text-[10px] text-slate-600 pl-1">
                    +{deck.cards.length - 3} more in this deck
                  </li>
                )}
              </ul>
            </li>
          ))}
        </ul>
      )}

      {!isLoading && dueCount > 0 && newCount > 0 && (
        <p className="text-[10px] text-slate-600 flex items-start gap-1.5">
          <AlertCircle className="h-3 w-3 mt-px shrink-0" />
          Rescheduling returns a card to today without touching your quiz mastery.
        </p>
      )}
    </div>
  );
}
