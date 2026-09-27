"use client";

import React, { useCallback, useEffect, useState } from "react";
import {
  api,
  AnalyticsOverview,
  Page,
  QuizAttemptSummary,
  QuizSummary,
} from "@/lib/api";
import { ChevronLeft, ChevronRight, History, ListChecks, Trophy } from "lucide-react";
import { Spinner } from "@/components/shared/Spinner";
import { ErrorBanner } from "@/components/shared/ErrorBanner";

interface HistorySectionProps {
  /** Scopes every panel to one subject; omit for the cross-subject dashboard. */
  subjectId?: string;
  /** Rows per page for the two history lists. */
  pageSize?: number;
  /** Rows requested for the timeline and the subject rollup. */
  timelineLimit?: number;
  onNavigateToSubject?: (subjectId: string) => void;
}

type Tab = "quizzes" | "attempts";

/** Which page of a paged list is on screen, and how far into it we are. */
interface Paging {
  offset: number;
  total: number;
  limit: number;
  hasMore: boolean;
}

const INITIAL_PAGING: Paging = { offset: 0, total: 0, limit: 20, hasMore: false };

function formatDate(value: string): string {
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime())
    ? value
    : parsed.toLocaleDateString(undefined, {
        year: "numeric",
        month: "short",
        day: "numeric",
      });
}

function formatPercent(value: number): string {
  return `${Math.round(value * 10) / 10}%`;
}

function plural(count: number, singular: string, pluralForm: string): string {
  return count === 1 ? singular : pluralForm;
}

/** A score pill that reads as success or as "work to do". */
function ScorePill({ percentage }: { percentage: number }) {
  const tone =
    percentage >= 80
      ? "bg-emerald-500/10 text-emerald-300"
      : percentage >= 50
        ? "bg-amber-500/10 text-amber-300"
        : "bg-rose-500/10 text-rose-300";
  return (
    <span className={`inline-flex items-center px-2 py-0.5 rounded-full text-[10px] font-bold ${tone}`}>
      {formatPercent(percentage)}
    </span>
  );
}

function StatTile({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="p-3 rounded-2xl border border-slate-800 bg-slate-900/60">
      <p className="text-[10px] font-semibold uppercase tracking-wide text-slate-500">{label}</p>
      <p className="text-lg font-bold text-white mt-0.5">{value}</p>
      {hint && <p className="text-[10px] text-slate-600 mt-0.5">{hint}</p>}
    </div>
  );
}

/** One row of the progress timeline. */
function TimelineRow({ entry }: { entry: AnalyticsOverview["timeline"][number] }) {
  return (
    <li className="flex items-center gap-3 rounded-xl border border-slate-800/70 bg-slate-950/40 px-3 py-2.5">
      <div className="min-w-0 flex-1">
        <p className="text-[11px] font-semibold text-slate-200 truncate">{entry.quiz_title}</p>
        <p className="text-[10px] text-slate-600 truncate">
          {entry.subject_title} &middot; {formatDate(entry.completed_at)} &middot;{" "}
          {entry.score}/{entry.total_questions} correct
        </p>
      </div>
      <ScorePill percentage={entry.percentage} />
    </li>
  );
}

/** Previous/next controls for a bounded page. */
function PaginationControls({
  paging,
  count,
  onPage,
  busy,
  label,
}: {
  paging: Paging;
  /** Rows actually rendered, which is not always a full page. */
  count: number;
  onPage: (offset: number) => void;
  busy: boolean;
  label: string;
}) {
  const shownFrom = paging.total === 0 ? 0 : paging.offset + 1;
  const shownTo = paging.offset + count;
  const atStart = paging.offset === 0;
  const atEnd = !paging.hasMore;

  return (
    <div className="flex items-center justify-between gap-3 pt-1">
      <p className="text-[10px] text-slate-600" data-testid={`${label}-range`}>
        {paging.total === 0
          ? "No entries"
          : `Showing ${shownFrom}–${shownTo} of ${paging.total}`}
      </p>
      <div className="flex items-center gap-1.5">
        <button
          type="button"
          onClick={() => onPage(Math.max(0, paging.offset - paging.limit))}
          disabled={atStart || busy}
          aria-label={`Previous page of ${label.toLowerCase()}`}
          className="inline-flex items-center p-1.5 rounded-lg border border-slate-800 text-slate-400 hover:text-white hover:border-slate-600 disabled:opacity-30 disabled:hover:text-slate-400 disabled:hover:border-slate-800 transition-colors"
        >
          <ChevronLeft className="h-3.5 w-3.5" />
        </button>
        <button
          type="button"
          onClick={() => onPage(paging.offset + paging.limit)}
          disabled={atEnd || busy}
          aria-label={`Next page of ${label.toLowerCase()}`}
          className="inline-flex items-center p-1.5 rounded-lg border border-slate-800 text-slate-400 hover:text-white hover:border-slate-600 disabled:opacity-30 disabled:hover:text-slate-400 disabled:hover:border-slate-800 transition-colors"
        >
          <ChevronRight className="h-3.5 w-3.5" />
        </button>
      </div>
    </div>
  );
}

/**
 * "Your learning history": the record of work already done.
 *
 * The dashboard's job is to make progress legible, so this is a read-only view
 * of the student's own history rather than a place to start more work. Three
 * panels share one page of state: lifetime totals, a recent-activity timeline,
 * and paginated quiz/attempt history.
 *
 * Every list is a bounded page from the API. `total` describes the whole
 * collection, so the range readout stays honest and the controls are enabled
 * from the server's answer instead of guessing from a short page.
 */
export function HistorySection({
  subjectId,
  pageSize = 20,
  timelineLimit = 10,
  onNavigateToSubject,
}: HistorySectionProps) {
  const [overview, setOverview] = useState<AnalyticsOverview | null>(null);
  const [isLoadingOverview, setIsLoadingOverview] = useState(true);
  const [overviewError, setOverviewError] = useState<string | null>(null);

  const [tab, setTab] = useState<Tab>("quizzes");
  const [quizzes, setQuizzes] = useState<QuizSummary[]>([]);
  const [attempts, setAttempts] = useState<QuizAttemptSummary[]>([]);
  const [quizPaging, setQuizPaging] = useState<Paging>(INITIAL_PAGING);
  const [attemptPaging, setAttemptPaging] = useState<Paging>(INITIAL_PAGING);
  const [isLoadingList, setIsLoadingList] = useState(true);
  const [listError, setListError] = useState<string | null>(null);

  const loadOverview = useCallback(async () => {
    try {
      const data = await api.getAnalyticsOverview({ subjectId, limit: timelineLimit });
      setOverview(data);
      setOverviewError(null);
    } catch (err: any) {
      setOverviewError(err.message || "Failed to load your learning summary.");
    } finally {
      setIsLoadingOverview(false);
    }
  }, [subjectId, timelineLimit]);

  useEffect(() => {
    setIsLoadingOverview(true);
    loadOverview();
  }, [loadOverview]);

  const loadList = useCallback(
    async (activeTab: Tab, offset: number) => {
      try {
        if (activeTab === "quizzes") {
          const page: Page<QuizSummary> = await api.getQuizzes({
            subjectId,
            limit: pageSize,
            offset,
          });
          setQuizzes(page.items);
          setQuizPaging({
            offset: page.offset,
            total: page.total,
            limit: page.limit,
            hasMore: page.has_more,
          });
        } else {
          const page: Page<QuizAttemptSummary> = await api.getQuizAttempts({
            subjectId,
            limit: pageSize,
            offset,
          });
          setAttempts(page.items);
          setAttemptPaging({
            offset: page.offset,
            total: page.total,
            limit: page.limit,
            hasMore: page.has_more,
          });
        }
        setListError(null);
      } catch (err: any) {
        setListError(err.message || "Failed to load your history.");
      } finally {
        setIsLoadingList(false);
      }
    },
    [subjectId, pageSize],
  );

  useEffect(() => {
    setIsLoadingList(true);
    loadList(tab, 0);
  }, [tab, loadList]);

  const changePage = (offset: number) => {
    setIsLoadingList(true);
    loadList(tab, offset);
  };

  const totals = overview?.totals;
  const hasHistory = (totals?.attempt_count ?? 0) > 0 || (totals?.quiz_count ?? 0) > 0;
  const paging = tab === "quizzes" ? quizPaging : attemptPaging;
  const visibleItems = tab === "quizzes" ? quizzes : attempts;

  return (
    <div className="space-y-4" data-testid="history-section">
      <div className="p-4 sm:p-5 rounded-2xl border border-slate-800 bg-slate-900/60 space-y-3">
        <div className="flex items-center justify-between gap-3">
          <h3 className="text-sm font-bold text-white flex items-center gap-2">
            <History className="h-4 w-4 text-sky-400" /> Your learning history
          </h3>
          {hasHistory && (
            <span className="inline-flex items-center px-2.5 py-1 rounded-full bg-sky-500/10 text-sky-300 text-[10px] font-semibold shrink-0">
              {totals?.attempt_count} {plural(totals?.attempt_count ?? 0, "attempt", "attempts")}
            </span>
          )}
        </div>

        {isLoadingOverview ? (
          <div className="flex items-center justify-center py-4">
            <Spinner />
          </div>
        ) : overviewError ? (
          <ErrorBanner message={overviewError} />
        ) : (
          <>
            <div className="grid grid-cols-2 lg:grid-cols-4 gap-2.5">
              <StatTile
                label="Attempts"
                value={String(totals?.attempt_count ?? 0)}
                hint={`${totals?.questions_answered ?? 0} questions answered`}
              />
              <StatTile
                label="Average score"
                value={formatPercent(totals?.average_percentage ?? 0)}
                hint={`Best ${formatPercent(totals?.best_percentage ?? 0)}`}
              />
              <StatTile
                label="Accuracy"
                value={formatPercent(totals?.accuracy ?? 0)}
                hint={`${totals?.questions_correct ?? 0} correct of ${totals?.questions_answered ?? 0}`}
              />
              <StatTile
                label="Quizzes"
                value={String(totals?.quiz_count ?? 0)}
                hint={
                  totals?.last_activity_at
                    ? `Last activity ${formatDate(totals.last_activity_at)}`
                    : "No activity yet"
                }
              />
            </div>

            {!hasHistory && (
              <p className="text-[11px] text-slate-500">
                Nothing here yet. Generate a quiz from a subject and your scores, attempts, and
                timeline will build up automatically.
              </p>
            )}
          </>
        )}
      </div>

      {!isLoadingOverview && !overviewError && overview && overview.timeline.length > 0 && (
        <div className="p-4 sm:p-5 rounded-2xl border border-slate-800 bg-slate-900/60 space-y-3">
          <h4 className="text-xs font-bold text-white">Recent activity</h4>
          <ul className="space-y-2">
            {overview.timeline.map((entry) => (
              <TimelineRow key={entry.id} entry={entry} />
            ))}
          </ul>
        </div>
      )}

      <div className="p-4 sm:p-5 rounded-2xl border border-slate-800 bg-slate-900/60 space-y-3">
        <div className="flex items-center gap-1.5" role="tablist" aria-label="History type">
          {(
            [
              { key: "quizzes" as Tab, label: "Quizzes" },
              { key: "attempts" as Tab, label: "Attempts" },
            ] satisfies { key: Tab; label: string }[]
          ).map((entry) => (
            <button
              key={entry.key}
              type="button"
              role="tab"
              aria-selected={tab === entry.key}
              onClick={() => setTab(entry.key)}
              className={`px-3 py-1.5 rounded-xl text-[11px] font-semibold transition-colors ${
                tab === entry.key
                  ? "bg-sky-500/15 text-sky-300"
                  : "text-slate-500 hover:text-slate-300"
              }`}
            >
              {entry.label}
            </button>
          ))}
        </div>

        {isLoadingList ? (
          <div className="flex items-center justify-center py-4">
            <Spinner />
          </div>
        ) : listError ? (
          <ErrorBanner message={listError} />
        ) : visibleItems.length === 0 ? (
          <p className="text-[11px] text-slate-500">
            {tab === "quizzes"
              ? "No quizzes generated yet."
              : "No quiz attempts yet."}
          </p>
        ) : (
          <ul className="space-y-2" data-testid={`history-list-${tab}`}>
            {tab === "quizzes"
              ? (quizzes as QuizSummary[]).map((quiz) => (
                  <li
                    key={quiz.id}
                    className="flex items-center gap-3 rounded-xl border border-slate-800/70 bg-slate-950/40 px-3 py-2.5"
                  >
                    <div className="min-w-0 flex-1">
                      <p className="text-[11px] font-semibold text-slate-200 truncate">{quiz.title}</p>
                      <p className="text-[10px] text-slate-600 truncate">
                        {quiz.subject_title} &middot; {quiz.question_count} questions &middot;{" "}
                        {quiz.difficulty}
                      </p>
                    </div>
                    {quiz.attempt_count > 0 ? (
                      <div className="flex items-center gap-2 shrink-0">
                        <span className="text-[10px] text-slate-500">
                          {quiz.attempt_count}{" "}
                          {plural(quiz.attempt_count, "attempt", "attempts")}
                        </span>
                        <ScorePill percentage={quiz.best_percentage} />
                      </div>
                    ) : (
                      <span className="text-[10px] text-slate-600 shrink-0">Not attempted</span>
                    )}
                  </li>
                ))
              : (attempts as QuizAttemptSummary[]).map((attempt) => (
                  <li
                    key={attempt.id}
                    className="flex items-center gap-3 rounded-xl border border-slate-800/70 bg-slate-950/40 px-3 py-2.5"
                  >
                    <div className="min-w-0 flex-1">
                      <p className="text-[11px] font-semibold text-slate-200 truncate">
                        {attempt.quiz_title}
                      </p>
                      <p className="text-[10px] text-slate-600 truncate">
                        {attempt.subject_title} &middot; {formatDate(attempt.completed_at)} &middot;{" "}
                        {attempt.score}/{attempt.total_questions}
                      </p>
                    </div>
                    <ScorePill percentage={attempt.percentage} />
                  </li>
                ))}
          </ul>
        )}

        <PaginationControls
          paging={paging}
          count={visibleItems.length}
          onPage={changePage}
          busy={isLoadingList}
          label={tab === "quizzes" ? "Quiz history" : "Attempt history"}
        />
      </div>

      {!isLoadingOverview && !overviewError && overview && overview.subjects.length > 0 && (
        <div className="p-4 sm:p-5 rounded-2xl border border-slate-800 bg-slate-900/60 space-y-2.5">
          <h4 className="text-xs font-bold text-white flex items-center gap-2">
            <Trophy className="h-3.5 w-3.5 text-amber-400" /> Subjects in progress
          </h4>
          <ul className="space-y-1.5">
            {overview.subjects.map((entry) => (
              <li key={entry.subject_id}>
                <button
                  type="button"
                  onClick={() => onNavigateToSubject?.(entry.subject_id)}
                  disabled={!onNavigateToSubject}
                  className="w-full flex items-center gap-3 rounded-xl border border-slate-800/70 bg-slate-950/40 px-3 py-2 text-left hover:border-slate-600 transition-colors disabled:hover:border-slate-800/70 disabled:cursor-default"
                >
                  <div className="min-w-0 flex-1">
                    <p className="text-[11px] font-semibold text-slate-200 truncate">
                      {entry.subject_title}
                    </p>
                    <p className="text-[10px] text-slate-600">
                      {entry.attempt_count}{" "}
                      {plural(entry.attempt_count, "attempt", "attempts")} &middot;{" "}
                      {formatPercent(entry.average_percentage)} average
                    </p>
                  </div>
                  <ScorePill percentage={entry.best_percentage} />
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}

      {!isLoadingOverview && !overviewError && overview && overview.weakest_topics.length > 0 && (
        <div className="p-4 sm:p-5 rounded-2xl border border-slate-800 bg-slate-900/60 space-y-2.5">
          <h4 className="text-xs font-bold text-white flex items-center gap-2">
            <ListChecks className="h-3.5 w-3.5 text-rose-400" /> Topics to revisit
          </h4>
          <ul className="space-y-1.5">
            {overview.weakest_topics.map((topic) => (
              <li
                key={topic.id}
                className="flex items-center gap-3 rounded-xl border border-slate-800/70 bg-slate-950/40 px-3 py-2"
              >
                <div className="min-w-0 flex-1">
                  <p className="text-[11px] font-semibold text-slate-200 truncate">{topic.topic}</p>
                  <p className="text-[10px] text-slate-600">
                    {topic.mastery_level} &middot; {topic.attempts}{" "}
                    {plural(topic.attempts, "attempt", "attempts")}
                  </p>
                </div>
                <ScorePill percentage={topic.accuracy} />
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
