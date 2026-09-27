"use client";

import React, { useCallback, useEffect, useMemo, useState } from "react";
import {
  api,
  FlashcardDeck,
  FlashcardDeckSummary,
  FlashcardReviewSummary,
  FlashcardTopicProgress,
} from "@/lib/api";
import {
  Layers,
  Sparkles,
  Trash2,
  ChevronLeft,
  ChevronRight,
  Eye,
  CheckCircle2,
  XCircle,
  RotateCcw,
  FileText,
  Lightbulb,
  Target,
} from "lucide-react";
import { Spinner } from "@/components/shared/Spinner";
import { ErrorBanner } from "@/components/shared/ErrorBanner";
import { EmptyState } from "@/components/shared/EmptyState";

interface FlashcardSectionProps {
  subjectId: string;
  hasReadyDocuments: boolean;
  initialTopic?: string;
  /** Fired after a deck is reviewed so the page can refresh sibling sections. */
  onReviewComplete?: () => void;
}

type View = "browse" | "reviewing" | "summary";

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
  const [summary, setSummary] = useState<FlashcardReviewSummary | null>(null);

  const [currentIndex, setCurrentIndex] = useState(0);
  const [isFlipped, setIsFlipped] = useState(false);
  // Self-graded answers for the current pass, keyed by card id.
  const [grades, setGrades] = useState<Record<string, boolean>>({});
  const [showHint, setShowHint] = useState(false);

  const [isLoadingDecks, setIsLoadingDecks] = useState(true);
  const [isGenerating, setIsGenerating] = useState(false);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const currentCard = deck?.cards[currentIndex] ?? null;
  const isLastCard = deck !== null && currentIndex >= deck.cards.length - 1;
  const gradedCount = useMemo(() => Object.keys(grades).length, [grades]);

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
    setSummary(null);
    setCurrentIndex(0);
    setIsFlipped(false);
    setGrades({});
    setShowHint(false);
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
    setCurrentIndex(0);
    setIsFlipped(false);
    setGrades({});
    setShowHint(false);
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

  const handleGrade = (isCorrect: boolean) => {
    if (!currentCard) return;
    setGrades((prev) => ({ ...prev, [currentCard.id]: isCorrect }));
    if (!isLastCard) {
      setCurrentIndex((prev) => prev + 1);
      setIsFlipped(false);
      setShowHint(false);
    }
  };

  const handlePrevious = () => {
    if (currentIndex === 0) return;
    setCurrentIndex((prev) => prev - 1);
    setIsFlipped(false);
    setShowHint(false);
  };

  const handleNext = () => {
    if (!deck || currentIndex >= deck.cards.length - 1) return;
    setCurrentIndex((prev) => prev + 1);
    setIsFlipped(false);
    setShowHint(false);
  };

  const handleSubmit = async () => {
    if (!deck) return;
    const reviews = Object.entries(grades).map(([cardId, isCorrect]) => ({
      card_id: cardId,
      is_correct: isCorrect,
    }));
    if (reviews.length === 0) return;

    setIsSubmitting(true);
    setError(null);
    try {
      const result = await api.reviewFlashcardDeck(deck.id, reviews);
      setSummary(result);
      setView("summary");
      await loadDecks();
      onReviewComplete?.();
    } catch (err: any) {
      setError(err.message || "Failed to save your review. Please try again.");
    } finally {
      setIsSubmitting(false);
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

  const handleRestart = () => {
    if (!deck) return;
    setGrades({});
    setCurrentIndex(0);
    setIsFlipped(false);
    setShowHint(false);
    setSummary(null);
    setView("reviewing");
  };

  if (view === "reviewing" && deck && currentCard) {
    const alreadyGraded = grades[currentCard.id];
    return (
      <div className="space-y-4">
        {error && <ErrorBanner message={error} />}

        {/* Progress header */}
        <div className="p-4 sm:p-5 rounded-2xl border border-slate-800 bg-slate-900/60 space-y-3">
          <div className="flex items-center justify-between gap-3">
            <div className="min-w-0">
              <p className="text-sm font-bold text-white truncate">{deck.title}</p>
              <p className="text-[11px] text-slate-500 mt-0.5">
                Card {currentIndex + 1} of {deck.cards.length} • Grade yourself after revealing the answer
              </p>
            </div>
            <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full bg-slate-800 text-slate-300 text-[10px] font-semibold shrink-0">
              <Target className="h-3 w-3 text-sky-400" /> {gradedCount}/{deck.cards.length} graded
            </span>
          </div>
          <div className="h-1.5 rounded-full bg-slate-800 overflow-hidden">
            <div
              className="h-full rounded-full bg-gradient-to-r from-sky-500 to-emerald-500 transition-all duration-300"
              style={{ width: `${((currentIndex + 1) / deck.cards.length) * 100}%` }}
            />
          </div>
        </div>

        {/* Card */}
        <div className="rounded-2xl border border-slate-800 bg-slate-900/60 p-4 sm:p-6 space-y-5">
          <p className="text-sm sm:text-base font-bold text-white leading-relaxed">
            {currentCard.front_text}
          </p>

          {!isFlipped ? (
            <div className="space-y-3">
              {currentCard.hint && (
                <div className="rounded-xl border border-amber-800/40 bg-amber-950/20 p-3 space-y-2">
                  <p className="text-xs font-semibold text-amber-300 flex items-center gap-1.5">
                    <Lightbulb className="h-3.5 w-3.5" /> Hint
                  </p>
                  {showHint ? (
                    <p className="text-xs text-amber-200/80 leading-relaxed">{currentCard.hint}</p>
                  ) : (
                    <button
                      onClick={() => setShowHint(true)}
                      className="text-[11px] font-semibold text-amber-400/80 hover:text-amber-300 transition-colors"
                    >
                      Show hint
                    </button>
                  )}
                </div>
              )}

              <button
                onClick={() => setIsFlipped(true)}
                className="w-full inline-flex items-center justify-center gap-2 px-4 py-2.5 rounded-xl bg-gradient-to-r from-sky-600 to-cyan-600 text-white text-xs font-semibold shadow-lg shadow-sky-600/25 hover:from-sky-500 hover:to-cyan-500 transition-all"
              >
                <Eye className="h-3.5 w-3.5" /> Reveal answer
              </button>
            </div>
          ) : (
            <div className="space-y-4">
              <div className="rounded-xl border border-emerald-800/50 bg-emerald-950/20 p-4">
                <p className="text-[11px] font-bold text-emerald-400 uppercase tracking-wide mb-1.5">
                  Answer
                </p>
                <p className="text-sm text-slate-100 leading-relaxed">{currentCard.back_text}</p>
              </div>

              {currentCard.source_snippet && (
                <div className="rounded-xl border border-slate-800 bg-slate-950/40 p-3 space-y-1.5">
                  <p className="text-[11px] font-bold text-slate-400 uppercase tracking-wide flex items-center gap-1.5">
                    <FileText className="h-3 w-3" />
                    Source
                    {currentCard.source_document_title && (
                      <span className="font-semibold normal-case text-slate-300">
                        {currentCard.source_document_title}
                        {currentCard.source_page_number ? `, p. ${currentCard.source_page_number}` : ""}
                      </span>
                    )}
                  </p>
                  <p className="text-[11px] text-slate-500 leading-relaxed italic">
                    {currentCard.source_snippet}
                  </p>
                </div>
              )}

              <div className="grid grid-cols-2 gap-3">
                <button
                  onClick={() => handleGrade(false)}
                  className={`inline-flex items-center justify-center gap-2 px-4 py-2.5 rounded-xl border text-xs font-semibold transition-colors ${
                    alreadyGraded === false
                      ? "border-rose-600 bg-rose-900/50 text-rose-200 ring-2 ring-rose-500/20"
                      : "border-rose-800/60 bg-rose-950/40 text-rose-300 hover:bg-rose-900/40"
                  }`}
                >
                  <XCircle className="h-3.5 w-3.5" /> Still learning
                </button>
                <button
                  onClick={() => handleGrade(true)}
                  className={`inline-flex items-center justify-center gap-2 px-4 py-2.5 rounded-xl border text-xs font-semibold transition-colors ${
                    alreadyGraded === true
                      ? "border-emerald-600 bg-emerald-900/50 text-emerald-200 ring-2 ring-emerald-500/20"
                      : "border-emerald-800/60 bg-emerald-950/40 text-emerald-300 hover:bg-emerald-900/40"
                  }`}
                >
                  <CheckCircle2 className="h-3.5 w-3.5" /> I knew it
                </button>
              </div>
              {alreadyGraded !== undefined && (
                <p className="text-[11px] text-slate-500 text-center">
                  Your last selection for this card is saved.
                </p>
              )}
            </div>
          )}
        </div>

        {/* Navigation */}
        <div className="flex items-center justify-between gap-3">
          <button
            onClick={handlePrevious}
            disabled={currentIndex === 0}
            className="inline-flex items-center gap-2 px-4 py-2.5 rounded-xl border border-slate-700 bg-slate-800/60 text-slate-300 hover:text-white text-xs font-semibold disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
          >
            <ChevronLeft className="h-3.5 w-3.5" /> Previous
          </button>

          <button
            onClick={resetSession}
            className="inline-flex items-center gap-2 px-3 py-2 text-xs text-slate-500 hover:text-slate-300 transition-colors"
          >
            <RotateCcw className="h-3.5 w-3.5" /> Back to decks
          </button>

          {isLastCard ? (
            <button
              onClick={handleSubmit}
              disabled={isSubmitting || gradedCount === 0}
              className="inline-flex items-center gap-2 px-5 py-2.5 rounded-xl bg-gradient-to-r from-sky-600 to-emerald-600 text-white text-xs font-semibold shadow-lg shadow-sky-600/25 hover:from-sky-500 hover:to-emerald-500 disabled:opacity-50 disabled:cursor-not-allowed transition-all"
            >
              {isSubmitting ? <Spinner className="h-3.5 w-3.5" /> : <CheckCircle2 className="h-3.5 w-3.5" />}
              {isSubmitting ? "Saving..." : "Finish review"}
            </button>
          ) : (
            <button
              onClick={handleNext}
              className="inline-flex items-center gap-2 px-4 py-2.5 rounded-xl bg-slate-800 text-white text-xs font-semibold hover:bg-slate-700 transition-colors"
            >
              Next <ChevronRight className="h-3.5 w-3.5" />
            </button>
          )}
        </div>
      </div>
    );
  }

  if (view === "summary" && summary) {
    return (
      <div className="space-y-5">
        {error && <ErrorBanner message={error} />}

        <div className="rounded-3xl border border-slate-800 bg-slate-900/60 p-6 sm:p-8 text-center space-y-3">
          <div
            className={`inline-flex h-24 w-24 items-center justify-center rounded-full border-4 ${
              summary.percentage >= 70
                ? "border-emerald-500/40 bg-emerald-950/40 text-emerald-400"
                : "border-amber-500/40 bg-amber-950/40 text-amber-400"
            }`}
          >
            <span className="text-2xl font-extrabold">{Math.round(summary.percentage)}%</span>
          </div>
          <p className="text-sm font-bold text-white">Review saved</p>
          <p className="text-xs text-slate-500">
            {summary.correct_count} correct • {summary.incorrect_count} to revisit
          </p>
        </div>

        {summary.results
          .filter((r) => !r.is_correct)
          .map((r) => {
            const card = deck?.cards.find((c) => c.id === r.card_id);
            if (!card) return null;
            return (
              <div
                key={r.card_id}
                className="rounded-2xl border border-rose-800/40 bg-rose-950/10 p-4 space-y-2"
              >
                <p className="text-xs font-bold text-rose-300">{card.front_text}</p>
                <p className="text-xs text-slate-300 leading-relaxed">{card.back_text}</p>
                {card.source_document_title && (
                  <p className="text-[11px] text-slate-500 flex items-center gap-1.5">
                    <FileText className="h-3 w-3" /> {card.source_document_title}
                  </p>
                )}
              </div>
            );
          })}

        <div className="flex items-center justify-center gap-3">
          <button
            onClick={resetSession}
            className="inline-flex items-center gap-2 px-4 py-2.5 rounded-xl border border-slate-700 bg-slate-800/60 text-slate-300 hover:text-white text-xs font-semibold transition-colors"
          >
            <Layers className="h-3.5 w-3.5" /> Back to decks
          </button>
          <button
            onClick={handleRestart}
            className="inline-flex items-center gap-2 px-5 py-2.5 rounded-xl bg-gradient-to-r from-sky-600 to-cyan-600 text-white text-xs font-semibold shadow-lg shadow-sky-600/25 hover:from-sky-500 hover:to-cyan-500 transition-all"
          >
            <RotateCcw className="h-3.5 w-3.5" /> Review again
          </button>
        </div>
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
