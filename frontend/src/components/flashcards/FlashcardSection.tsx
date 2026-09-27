"use client";

import React, { useCallback, useEffect, useState } from "react";
import {
  api,
  FlashcardDeck,
  FlashcardDeckSummary,
  FlashcardTopicProgress,
} from "@/lib/api";
import { Layers, Sparkles, Trash2, Eye } from "lucide-react";
import { Spinner } from "@/components/shared/Spinner";
import { ErrorBanner } from "@/components/shared/ErrorBanner";
import { EmptyState } from "@/components/shared/EmptyState";
import { CardStudySession } from "@/components/flashcards/CardStudySession";

interface FlashcardSectionProps {
  subjectId: string;
  hasReadyDocuments: boolean;
  initialTopic?: string;
  /** Fired after a deck is reviewed so the page can refresh sibling sections. */
  onReviewComplete?: () => void;
}

type View = "browse" | "reviewing";

const CARD_COUNTS = [5, 10, 20];

export function FlashcardSection({
  subjectId,
  hasReadyDocuments,
  initialTopic,
  onReviewComplete,
}: FlashcardSectionProps) {
  const [view, setView] = useState<View>("browse");
  const [cardCount, setCardCount] = useState(10);
  const [topic, setTopic] = useState(initialTopic ?? "");

  const [decks, setDecks] = useState<FlashcardDeckSummary[]>([]);
  const [progress, setProgress] = useState<FlashcardTopicProgress[]>([]);
  const [deck, setDeck] = useState<FlashcardDeck | null>(null);

  const [isLoadingDecks, setIsLoadingDecks] = useState(true);
  const [isGenerating, setIsGenerating] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const loadDecks = useCallback(async () => {
    try {
      const [deckList, topicProgress] = await Promise.all([
        api.getFlashcardDecks(subjectId),
        api.getFlashcardProgress(subjectId),
      ]);
      setDecks(deckList);
      setProgress(topicProgress);
    } catch (err: any) {
      setError(err.message || "Failed to load flashcards.");
    } finally {
      setIsLoadingDecks(false);
    }
  }, [subjectId]);

  useEffect(() => {
    setIsLoadingDecks(true);
    loadDecks();
  }, [loadDecks]);

  const resetSession = () => {
    setDeck(null);
    setError(null);
    setView("browse");
  };

  const handleGenerate = async () => {
    if (!hasReadyDocuments) return;

    setIsGenerating(true);
    setError(null);
    try {
      const generated = await api.generateFlashcards(
        subjectId,
        cardCount,
        topic.trim() || null,
      );
      setDecks((prev) => [
        {
          id: generated.id,
          subject_id: generated.subject_id,
          title: generated.title,
          topic: generated.topic,
          card_count: generated.card_count,
          // Every card in a fresh deck is new, and new cards are due at once.
          due_count: generated.due_count ?? generated.card_count,
          created_at: generated.created_at,
          reviewed_card_count: 0,
          correct_count: 0,
        },
        ...prev,
      ]);
      startReview(generated);
    } catch (err: any) {
      setError(err.message || "Failed to generate flashcards. Please try again.");
    } finally {
      setIsGenerating(false);
    }
  };

  const startReview = (target: FlashcardDeck) => {
    setDeck(target);
    setError(null);
    setView("reviewing");
  };

  const handleOpenDeck = async (deckId: string) => {
    setError(null);
    try {
      const loaded = await api.getFlashcardDeck(deckId);
      startReview(loaded);
    } catch (err: any) {
      setError(err.message || "Failed to open that deck.");
    }
  };

  const handleDelete = async (deckId: string) => {
    setError(null);
    try {
      await api.deleteFlashcardDeck(deckId);
      setDecks((prev) => prev.filter((d) => d.id !== deckId));
      if (deck?.id === deckId) resetSession();
      await loadDecks();
    } catch (err: any) {
      setError(err.message || "Failed to delete that deck.");
    }
  };

  if (view === "reviewing" && deck) {
    return (
      <div className="space-y-4">
        <CardStudySession
          cards={deck.cards}
          title={deck.title}
          onExit={resetSession}
          onSubmit={async (reviews) => {
            const result = await api.reviewFlashcardDeck(deck.id, reviews);
            await loadDecks();
            onReviewComplete?.();
            return result;
          }}
        />
      </div>
    );
  }

  return (
    <div className="space-y-5">
      {error && <ErrorBanner message={error} />}

      {/* Generator */}
      <div className="p-4 sm:p-5 rounded-2xl border border-slate-800 bg-slate-900/60 space-y-4">
        <div>
          <h3 className="text-sm font-bold text-white flex items-center gap-2">
            <Sparkles className="h-4 w-4 text-sky-400" /> Generate a deck
          </h3>
          <p className="text-[11px] text-slate-500 mt-1">
            Cards are written from your own uploaded material, and every card keeps a citation back to it.
          </p>
        </div>

        <div className="flex flex-wrap items-end gap-3">
          <div className="space-y-1.5">
            <label htmlFor="flashcard-count" className="block text-[11px] font-semibold text-slate-400">
              Cards
            </label>
            <div className="flex gap-1.5">
              {CARD_COUNTS.map((count) => (
                <button
                  key={count}
                  onClick={() => setCardCount(count)}
                  aria-pressed={cardCount === count}
                  className={`px-3 py-1.5 rounded-lg text-xs font-semibold border transition-colors ${
                    cardCount === count
                      ? "border-sky-500 bg-sky-500/10 text-white"
                      : "border-slate-800 bg-slate-950/40 text-slate-400 hover:border-slate-600"
                  }`}
                >
                  {count}
                </button>
              ))}
            </div>
          </div>

          <div className="flex-1 min-w-[160px] space-y-1.5">
            <label htmlFor="flashcard-topic" className="block text-[11px] font-semibold text-slate-400">
              Topic <span className="text-slate-600 font-normal">(optional)</span>
            </label>
            <input
              id="flashcard-topic"
              type="text"
              value={topic}
              onChange={(e) => setTopic(e.target.value)}
              placeholder="e.g. Vector Databases"
              className="w-full px-3 py-2 rounded-xl border border-slate-800 bg-slate-950/60 text-sm text-white placeholder:text-slate-600 focus:border-sky-500 focus:outline-none transition-colors"
            />
          </div>
        </div>

        <button
          onClick={handleGenerate}
          disabled={isGenerating || !hasReadyDocuments}
          className="w-full sm:w-auto inline-flex items-center justify-center gap-2 px-5 py-2.5 rounded-xl bg-gradient-to-r from-sky-600 to-cyan-600 text-white text-xs font-semibold shadow-lg shadow-sky-600/25 hover:from-sky-500 hover:to-cyan-500 disabled:opacity-50 disabled:cursor-not-allowed transition-all"
        >
          {isGenerating ? <Spinner className="h-3.5 w-3.5" /> : <Sparkles className="h-3.5 w-3.5" />}
          {isGenerating ? "Generating..." : "Generate flashcards"}
        </button>

        {!hasReadyDocuments && (
          <p className="text-[11px] text-amber-400/80">
            Upload and process a document first — flashcards are generated from your material.
          </p>
        )}
      </div>

      {/* Existing decks */}
      <div className="space-y-3">
        {isLoadingDecks ? (
          <div className="flex items-center justify-center py-8 text-slate-500">
            <Spinner />
          </div>
        ) : decks.length === 0 ? (
          <EmptyState
            icon={<Layers className="h-5 w-5" />}
            title="No decks yet"
            description="Generate your first deck to start reviewing. Decks are private to you."
          />
        ) : (
          <ul className="space-y-2.5">
            {decks.map((d) => {
              const reviewed = Math.min(d.reviewed_card_count, d.card_count);
              const pct = d.card_count > 0 ? (reviewed / d.card_count) * 100 : 0;
              return (
                <li
                  key={d.id}
                  className="flex items-center gap-3 p-3.5 rounded-2xl border border-slate-800 bg-slate-900/60"
                >
                  <div className="min-w-0 flex-1 space-y-1.5">
                    <p className="text-xs font-bold text-white truncate">{d.title}</p>
                    <p className="text-[11px] text-slate-500">
                      {d.card_count} cards • {reviewed} reviewed • {d.correct_count} correct
                    </p>
                    <div className="h-1 rounded-full bg-slate-800 overflow-hidden">
                      <div
                        className="h-full rounded-full bg-sky-500 transition-all duration-300"
                        style={{ width: `${pct}%` }}
                      />
                    </div>
                  </div>
                  <div className="flex items-center gap-1.5 shrink-0">
                    {d.due_count > 0 && (
                      <span className="inline-flex items-center px-2 py-1 rounded-full bg-amber-500/10 text-amber-300 text-[10px] font-semibold">
                        {d.due_count} due
                      </span>
                    )}
                    <button
                      onClick={() => handleOpenDeck(d.id)}
                      className="inline-flex items-center gap-1.5 px-3 py-2 rounded-xl bg-slate-800 text-white text-[11px] font-semibold hover:bg-slate-700 transition-colors"
                    >
                      <Eye className="h-3.5 w-3.5" /> Review
                    </button>
                    <button
                      onClick={() => handleDelete(d.id)}
                      aria-label={`Delete ${d.title}`}
                      className="inline-flex items-center justify-center p-2 rounded-xl border border-slate-800 text-slate-500 hover:text-rose-400 hover:border-rose-800/60 transition-colors"
                    >
                      <Trash2 className="h-3.5 w-3.5" />
                    </button>
                  </div>
                </li>
              );
            })}
          </ul>
        )}
      </div>

      {/* Topic progress */}
      {progress.length > 0 && (
        <div className="p-4 sm:p-5 rounded-2xl border border-slate-800 bg-slate-900/60 space-y-3">
          <h3 className="text-xs font-bold text-white">Progress by topic</h3>
          <ul className="space-y-2">
            {progress.map((row) => (
              <li
                key={`${row.subject_id}:${row.topic}`}
                className="flex items-center justify-between gap-3 text-[11px]"
              >
                <span className="min-w-0 truncate text-slate-300">{row.topic}</span>
                <span className="flex items-center gap-2 shrink-0 text-slate-500">
                  <span>
                    {row.reviewed_count}/{row.card_count} • {Math.round(row.accuracy)}%
                  </span>
                  {row.mastery_level && (
                    <span className="inline-flex items-center px-2 py-0.5 rounded-full bg-slate-800 text-slate-300 capitalize">
                      {row.mastery_level}
                    </span>
                  )}
                </span>
              </li>
            ))}
          </ul>
          <p className="text-[10px] text-slate-600">
            Mastery comes from your quiz history; reviewing flashcards never changes it.
          </p>
        </div>
      )}
    </div>
  );
}
