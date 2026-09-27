"use client";

import React, { useMemo, useState } from "react";
import {
  api,
  Flashcard,
  FlashcardReviewSummary,
} from "@/lib/api";
import {
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

interface CardStudySessionProps {
  /** The cards to study, already ordered by the caller (most overdue first). */
  cards: Flashcard[];
  /** Header text, e.g. the deck title or "Due today". */
  title: string;
  /** Post the accumulated grades. Receives one result per card in `cards`. */
  onSubmit: (reviews: { card_id: string; is_correct: boolean }[]) => Promise<FlashcardReviewSummary>;
  /** Back to whatever list the student came from. */
  onExit: () => void;
  /**
   * Extra guidance shown under the card, e.g. how many cards are still to come
   * in this session.
   */
  footerNote?: string;
}

/**
 * The self-graded review loop, shared by deck browsing and the due queue.
 *
 * Owning this in one place is the point: the reveal-then-grade interaction is
 * identical in both flows, and a student moving between "Due today" and a deck
 * should not have to relearn the UI. What differs between the flows -- which
 * cards are in the session, and what the summary does afterwards -- is passed
 * in from the caller.
 */
export function CardStudySession({
  cards,
  title,
  onSubmit,
  onExit,
  footerNote,
}: CardStudySessionProps) {
  const [currentIndex, setCurrentIndex] = useState(0);
  const [isFlipped, setIsFlipped] = useState(false);
  const [grades, setGrades] = useState<Record<string, boolean>>({});
  const [showHint, setShowHint] = useState(false);
  const [summary, setSummary] = useState<FlashcardReviewSummary | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const gradedCount = useMemo(() => Object.keys(grades).length, [grades]);
  const isLastCard = currentIndex >= cards.length - 1;
  const currentCard = cards[currentIndex];

  const resetPass = () => {
    setCurrentIndex(0);
    setIsFlipped(false);
    setGrades({});
    setShowHint(false);
    setSummary(null);
    setError(null);
  };

  const moveTo = (index: number) => {
    setCurrentIndex(index);
    setIsFlipped(false);
    setShowHint(false);
  };

  const handleGrade = (isCorrect: boolean) => {
    if (!currentCard) return;
    setGrades((prev) => ({ ...prev, [currentCard.id]: isCorrect }));
    if (!isLastCard) moveTo(currentIndex + 1);
  };

  const handleSubmit = async () => {
    if (gradedCount === 0) return;
    setIsSubmitting(true);
    setError(null);
    try {
      const reviews = cards.map((card) => ({
        card_id: card.id,
        // Ungraded cards count as not-yet-known rather than being silently
        // dropped: the API requires one result per card, and guessing "correct"
        // would stretch an interval the student never earned.
        is_correct: grades[card.id] ?? false,
      }));
      setSummary(await onSubmit(reviews));
    } catch (err: any) {
      setError(err.message || "Failed to save your review. Please try again.");
    } finally {
      setIsSubmitting(false);
    }
  };

  if (summary) {
    const toRevisit = summary.results.filter((r) => !r.is_correct);
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

        {toRevisit.map((r) => {
          const card = cards.find((c) => c.id === r.card_id);
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
              <p className="text-[11px] text-sky-400">
                Back in {describeInterval(r.schedule.interval_days)} — a lapse resets the
                interval to one day.
              </p>
            </div>
          );
        })}

        <div className="flex items-center justify-center gap-3">
          <button
            onClick={onExit}
            className="inline-flex items-center gap-2 px-4 py-2.5 rounded-xl border border-slate-700 bg-slate-800/60 text-slate-300 hover:text-white text-xs font-semibold transition-colors"
          >
            Done
          </button>
          <button
            onClick={resetPass}
            className="inline-flex items-center gap-2 px-5 py-2.5 rounded-xl bg-gradient-to-r from-sky-600 to-cyan-600 text-white text-xs font-semibold shadow-lg shadow-sky-600/25 hover:from-sky-500 hover:to-cyan-500 transition-all"
          >
            <RotateCcw className="h-3.5 w-3.5" /> Review again
          </button>
        </div>
      </div>
    );
  }

  const alreadyGraded = grades[currentCard?.id ?? ""];

  return (
    <div className="space-y-4">
      {error && <ErrorBanner message={error} />}

      <div className="p-4 sm:p-5 rounded-2xl border border-slate-800 bg-slate-900/60 space-y-3">
        <div className="flex items-center justify-between gap-3">
          <div className="min-w-0">
            <p className="text-sm font-bold text-white truncate">{title}</p>
            <p className="text-[11px] text-slate-500 mt-0.5">
              Card {currentIndex + 1} of {cards.length} • Grade yourself after revealing the
              answer
            </p>
          </div>
          <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full bg-slate-800 text-slate-300 text-[10px] font-semibold shrink-0">
            <Target className="h-3 w-3 text-sky-400" /> {gradedCount}/{cards.length} graded
          </span>
        </div>
        <div className="h-1.5 rounded-full bg-slate-800 overflow-hidden">
          <div
            className="h-full rounded-full bg-gradient-to-r from-sky-500 to-emerald-500 transition-all duration-300"
            style={{ width: `${((currentIndex + 1) / cards.length) * 100}%` }}
          />
        </div>
      </div>

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
                  <p className="text-xs text-amber-200/80 leading-relaxed">
                    {currentCard.hint}
                  </p>
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
              <p className="text-sm text-slate-100 leading-relaxed">
                {currentCard.back_text}
              </p>
            </div>

            {currentCard.source_snippet && (
              <div className="rounded-xl border border-slate-800 bg-slate-950/40 p-3 space-y-1.5">
                <p className="text-[11px] font-bold text-slate-400 uppercase tracking-wide flex items-center gap-1.5">
                  <FileText className="h-3 w-3" />
                  Source
                  {currentCard.source_document_title && (
                    <span className="font-semibold normal-case text-slate-300">
                      {currentCard.source_document_title}
                      {currentCard.source_page_number
                        ? `, p. ${currentCard.source_page_number}`
                        : ""}
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

      <div className="flex items-center justify-between gap-3">
        <button
          onClick={() => currentIndex > 0 && moveTo(currentIndex - 1)}
          disabled={currentIndex === 0}
          className="inline-flex items-center gap-2 px-4 py-2.5 rounded-xl border border-slate-700 bg-slate-800/60 text-slate-300 hover:text-white text-xs font-semibold disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
        >
          <ChevronLeft className="h-3.5 w-3.5" /> Previous
        </button>

        <button
          onClick={onExit}
          className="inline-flex items-center gap-2 px-3 py-2 text-xs text-slate-500 hover:text-slate-300 transition-colors"
        >
          <RotateCcw className="h-3.5 w-3.5" /> Back
        </button>

        {isLastCard ? (
          <button
            onClick={handleSubmit}
            disabled={isSubmitting || gradedCount === 0}
            className="inline-flex items-center gap-2 px-5 py-2.5 rounded-xl bg-gradient-to-r from-sky-600 to-emerald-600 text-white text-xs font-semibold shadow-lg shadow-sky-600/25 hover:from-sky-500 hover:to-emerald-500 disabled:opacity-50 disabled:cursor-not-allowed transition-all"
          >
            {isSubmitting ? (
              <Spinner className="h-3.5 w-3.5" />
            ) : (
              <CheckCircle2 className="h-3.5 w-3.5" />
            )}
            {isSubmitting ? "Saving..." : "Finish review"}
          </button>
        ) : (
          <button
            onClick={() => moveTo(currentIndex + 1)}
            className="inline-flex items-center gap-2 px-4 py-2.5 rounded-xl bg-slate-800 text-white text-xs font-semibold hover:bg-slate-700 transition-colors"
          >
            Next <ChevronRight className="h-3.5 w-3.5" />
          </button>
        )}
      </div>

      {footerNote && <p className="text-[11px] text-slate-500 text-center">{footerNote}</p>}
    </div>
  );
}

/** Renders an interval in days the way a student would say it. */
export function describeInterval(days: number): string {
  if (days <= 1) return "1 day";
  if (days < 30) return `${days} days`;
  if (days < 365) {
    const months = Math.round(days / 30);
    return `${months} month${months === 1 ? "" : "s"}`;
  }
  const years = Math.round(days / 365);
  return `${years} year${years === 1 ? "" : "s"}`;
}
