"use client";

import React, { useEffect, useState } from "react";
import { api, SubjectPerformance, TopicMastery, WeakTopic } from "@/lib/api";
import { Spinner } from "@/components/shared/Spinner";
import { ErrorBanner } from "@/components/shared/ErrorBanner";
import { EmptyState } from "@/components/shared/EmptyState";
import {
  BrainCircuit,
  Target,
  TrendingUp,
  Award,
  History,
  RefreshCw,
  Layers,
  Clock,
  AlertTriangle,
} from "lucide-react";

interface StudentIntelligenceProps {
  subjectId: string;
  refreshKey?: number;
}

const LEVEL_COLORS: Record<string, string> = {
  mastered: "bg-emerald-950/80 border-emerald-800/60 text-emerald-400",
  proficient: "bg-sky-950/80 border-sky-800/60 text-sky-400",
  developing: "bg-amber-950/80 border-amber-800/60 text-amber-400",
  novice: "bg-rose-950/80 border-rose-800/60 text-rose-400",
};

const LEVEL_BAR_COLORS: Record<string, string> = {
  mastered: "from-emerald-500 to-teal-500",
  proficient: "from-sky-500 to-blue-500",
  developing: "from-amber-500 to-orange-500",
  novice: "from-rose-500 to-red-500",
};

function masteryBarColor(level: string): string {
  return LEVEL_BAR_COLORS[level] ?? "from-slate-500 to-slate-400";
}

const REASON_LABELS: Record<string, string> = {
  repeated_mistakes: "Repeated mistakes",
  low_accuracy: "Low accuracy",
  recent_poor_performance: "Recent decline",
  insufficient_evidence: "Needs more evidence",
};

const REASON_STYLES: Record<string, string> = {
  repeated_mistakes: "bg-rose-950/80 border-rose-800/60 text-rose-400",
  low_accuracy: "bg-amber-950/80 border-amber-800/60 text-amber-400",
  recent_poor_performance: "bg-orange-950/80 border-orange-800/60 text-orange-400",
  insufficient_evidence: "bg-slate-900/80 border-slate-700/60 text-slate-400",
};

function reasonLabel(code: string): string {
  return REASON_LABELS[code] ?? code;
}

function formatDate(value: string | null): string {
  if (!value) return "—";
  return new Date(value).toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

export function StudentIntelligence({ subjectId, refreshKey = 0 }: StudentIntelligenceProps) {
  const [data, setData] = useState<SubjectPerformance | null>(null);
  const [weakTopics, setWeakTopics] = useState<WeakTopic[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = async () => {
    setIsLoading(true);
    setError(null);
    try {
      const performance = await api.getSubjectPerformance(subjectId);
      setData(performance);
    } catch (err: any) {
      setError(err.message || "Failed to load performance data.");
    } finally {
      setIsLoading(false);
    }
  };

  // Weak topics are supplementary: a failure here must not hide the
  // performance summary, so it degrades to an empty list.
  useEffect(() => {
    let active = true;
    api
      .getSubjectWeakTopics(subjectId)
      .then((topics) => {
        if (active) setWeakTopics(topics);
      })
      .catch(() => {
        if (active) setWeakTopics([]);
      });
    return () => {
      active = false;
    };
  }, [subjectId, refreshKey]);

  useEffect(() => {
    load();
  }, [subjectId, refreshKey]);

  if (isLoading && !data) {
    return (
      <div className="flex items-center justify-center gap-3 p-10 rounded-2xl border border-slate-800 bg-slate-900/60">
        <Spinner className="h-5 w-5" />
        <p className="text-xs text-slate-400">Loading your learning data...</p>
      </div>
    );
  }

  if (!data) {
    return (
      <div className="space-y-4">
        {error && <ErrorBanner message={error} />}
        <EmptyState
          icon={<BrainCircuit className="h-6 w-6" />}
          title="No performance data yet"
          description="Complete a quiz in the Quiz Engine above and your attempt, score, and per-topic mastery will be tracked here."
        />
      </div>
    );
  }

  const { summary, attempts, mastery } = data;
  const hasData = summary.total_attempts > 0;

  return (
    <div className="space-y-5">
      {error && <ErrorBanner message={error} />}

      <div className="flex items-center justify-end">
        <button
          onClick={load}
          disabled={isLoading}
          className="inline-flex items-center gap-2 px-3 py-1.5 rounded-lg border border-slate-700 bg-slate-800/60 text-slate-300 hover:text-white text-xs font-semibold disabled:opacity-50 transition-colors"
        >
          <RefreshCw className={`h-3.5 w-3.5 ${isLoading ? "animate-spin" : ""}`} /> Refresh
        </button>
      </div>

      {!hasData ? (
        <EmptyState
          icon={<BrainCircuit className="h-6 w-6" />}
          title="No attempts recorded yet"
          description="Submit a quiz to start building your student intelligence profile — scores, accuracy, and per-topic mastery."
        />
      ) : (
        <>
          {/* Summary Cards */}
          <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
            <div className="p-4 rounded-2xl border border-slate-800 bg-slate-900/40">
              <div className="flex items-center gap-2 text-slate-400">
                <History className="h-4 w-4 text-purple-400" />
                <p className="text-[11px] font-semibold uppercase tracking-wider">Attempts</p>
              </div>
              <p className="mt-1.5 text-2xl font-extrabold text-white">{summary.total_attempts}</p>
            </div>

            <div className="p-4 rounded-2xl border border-slate-800 bg-slate-900/40">
              <div className="flex items-center gap-2 text-slate-400">
                <Target className="h-4 w-4 text-sky-400" />
                <p className="text-[11px] font-semibold uppercase tracking-wider">Avg Score</p>
              </div>
              <p className="mt-1.5 text-2xl font-extrabold text-white">{summary.average_percentage}%</p>
            </div>

            <div className="p-4 rounded-2xl border border-slate-800 bg-slate-900/40">
              <div className="flex items-center gap-2 text-slate-400">
                <Award className="h-4 w-4 text-amber-400" />
                <p className="text-[11px] font-semibold uppercase tracking-wider">Best Score</p>
              </div>
              <p className="mt-1.5 text-2xl font-extrabold text-white">{summary.best_percentage}%</p>
            </div>

            <div className="p-4 rounded-2xl border border-slate-800 bg-slate-900/40">
              <div className="flex items-center gap-2 text-slate-400">
                <TrendingUp className="h-4 w-4 text-emerald-400" />
                <p className="text-[11px] font-semibold uppercase tracking-wider">Accuracy</p>
              </div>
              <p className="mt-1.5 text-2xl font-extrabold text-white">{summary.accuracy}%</p>
            </div>
          </div>

          {/* Mastery per Topic */}
          <div className="rounded-2xl border border-slate-800 bg-slate-900/60 p-4 sm:p-5 space-y-4">
            <div className="flex items-center gap-2">
              <BrainCircuit className="h-4 w-4 text-purple-400" />
              <h4 className="text-sm font-bold text-white">Topic Mastery</h4>
            </div>

            <div className="space-y-3">
              {mastery.length === 0 && (
                <p className="text-xs text-slate-500">Submit a quiz to start tracking mastery per topic.</p>
              )}
              {mastery.map((item) => (
                <MasteryRow key={item.id} item={item} />
              ))}
            </div>
          </div>

          {/* Needs Practice */}
          <div className="rounded-2xl border border-slate-800 bg-slate-900/60 p-4 sm:p-5 space-y-4">
            <div className="flex items-center gap-2">
              <AlertTriangle className="h-4 w-4 text-amber-400" />
              <h4 className="text-sm font-bold text-white">Needs Practice</h4>
            </div>

            {weakTopics.length === 0 ? (
              <p className="text-xs text-slate-500">
                No weak topics detected. Every topic you have answered looks solid.
              </p>
            ) : (
              <div className="space-y-2.5">
                {weakTopics.map((item) => (
                  <WeakTopicRow key={`${item.subject_id}-${item.topic}`} item={item} />
                ))}
              </div>
            )}
          </div>

          {/* Difficulty Breakdown */}
          {summary.difficulty_breakdown.length > 0 && (
            <div className="rounded-2xl border border-slate-800 bg-slate-900/60 p-4 sm:p-5 space-y-4">
              <div className="flex items-center gap-2">
                <Layers className="h-4 w-4 text-sky-400" />
                <h4 className="text-sm font-bold text-white">Difficulty Breakdown</h4>
              </div>

              <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
                {summary.difficulty_breakdown.map((item) => {
                  const accuracy = item.attempted ? Math.round((item.correct / item.attempted) * 100) : 0;
                  return (
                    <div key={item.difficulty} className="p-3 rounded-xl bg-slate-950/40 border border-slate-800">
                      <div className="flex items-center justify-between">
                        <p className="text-xs font-bold text-white capitalize">{item.difficulty}</p>
                        <p className="text-[11px] text-slate-400">
                          {item.correct}/{item.attempted} • {accuracy}%
                        </p>
                      </div>
                      <div className="mt-2 h-1.5 rounded-full bg-slate-800 overflow-hidden">
                        <div
                          className="h-full rounded-full bg-gradient-to-r from-sky-500 to-blue-500 transition-all duration-300"
                          style={{ width: `${accuracy}%` }}
                        />
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>
          )}

          {/* Recent Attempts */}
          <div className="rounded-2xl border border-slate-800 bg-slate-900/60 p-4 sm:p-5 space-y-4">
            <div className="flex items-center gap-2">
              <History className="h-4 w-4 text-emerald-400" />
              <h4 className="text-sm font-bold text-white">Recent Attempts</h4>
            </div>

            <div className="space-y-2.5">
              {attempts.map((attempt) => (
                <div
                  key={attempt.id}
                  className="flex items-center justify-between gap-3 p-3 rounded-xl bg-slate-950/40 border border-slate-800"
                >
                  <div className="flex items-center gap-3 min-w-0">
                    <Clock className="h-4 w-4 text-slate-500 shrink-0" />
                    <div className="min-w-0">
                      <p className="text-xs font-semibold text-white">
                        {attempt.score} / {attempt.total_questions} correct
                      </p>
                      <p className="text-[11px] text-slate-500">{formatDate(attempt.completed_at)}</p>
                    </div>
                  </div>
                  <span
                    className={`shrink-0 inline-flex items-center px-2.5 py-1 rounded-full text-[11px] font-bold border ${
                      attempt.percentage >= 70
                        ? "bg-emerald-950/80 border-emerald-800/60 text-emerald-400"
                        : attempt.percentage >= 40
                          ? "bg-amber-950/80 border-amber-800/60 text-amber-400"
                          : "bg-rose-950/80 border-rose-800/60 text-rose-400"
                    }`}
                  >
                    {attempt.percentage}%
                  </span>
                </div>
              ))}
            </div>
          </div>
        </>
      )}
    </div>
  );
}

function WeakTopicRow({ item }: { item: WeakTopic }) {
  const confirmed = item.confidence === "confirmed";
  return (
    <div className="p-3 rounded-xl bg-slate-950/40 border border-slate-800">
      <div className="flex items-center justify-between gap-3">
        <div className="flex items-center gap-2 min-w-0">
          <p className="text-xs font-bold text-white capitalize truncate">{item.topic}</p>
          <span
            title={confirmed ? "Confirmed by multiple attempts" : "Based on limited evidence"}
            className={`shrink-0 inline-flex items-center px-2 py-0.5 rounded-full border text-[10px] font-semibold ${
              confirmed
                ? "bg-emerald-950/80 border-emerald-800/60 text-emerald-400"
                : "bg-slate-900/80 border-slate-700/60 text-slate-400"
            }`}
          >
            {confirmed ? "Confirmed" : "Low confidence"}
          </span>
        </div>
        <span className="shrink-0 text-[11px] text-slate-400">
          {item.questions_correct}/{item.questions_attempted} • {item.accuracy}% accuracy
        </span>
      </div>
      <div className="mt-2 flex flex-wrap gap-1.5">
        {item.reasons.map((reason) => (
          <span
            key={reason.code}
            title={reason.message}
            className={`inline-flex items-center px-2 py-0.5 rounded-full border text-[10px] font-semibold ${
              REASON_STYLES[reason.code] ?? REASON_STYLES.insufficient_evidence
            }`}
          >
            {reasonLabel(reason.code)}
          </span>
        ))}
      </div>
      <ul className="mt-2 space-y-0.5">
        {item.reasons.map((reason) => (
          <li key={`${reason.code}-detail`} className="text-[10px] text-slate-500">
            {reason.message}
          </li>
        ))}
      </ul>
    </div>
  );
}

function MasteryRow({ item }: { item: TopicMastery }) {
  const level = item.mastery_level || "novice";
  return (
    <div className="p-3 rounded-xl bg-slate-950/40 border border-slate-800">
      <div className="flex items-center justify-between gap-3">
        <p className="text-xs font-bold text-white capitalize truncate">{item.topic}</p>
        <div className="flex items-center gap-2 shrink-0">
          <span className="text-[11px] text-slate-400">
            {item.questions_correct}/{item.questions_attempted} • {item.accuracy}% accuracy
          </span>
          <span
            className={`inline-flex items-center px-2.5 py-1 rounded-full border text-[10px] font-semibold capitalize ${LEVEL_COLORS[level] ?? LEVEL_COLORS.novice}`}
          >
            {level}
          </span>
        </div>
      </div>
      <div className="mt-2 flex items-center gap-3">
        <div className="flex-1 h-2 rounded-full bg-slate-800 overflow-hidden">
          <div
            className={`h-full rounded-full bg-gradient-to-r ${masteryBarColor(level)} transition-all duration-300`}
            style={{ width: `${Math.min(100, item.mastery_score)}%` }}
          />
        </div>
        <span className="w-12 text-right text-xs font-extrabold text-white">{item.mastery_score}</span>
      </div>
      <p className="mt-1.5 text-[10px] text-slate-500">
        {item.attempts} attempts • last {formatDate(item.last_attempt_at)}
      </p>
    </div>
  );
}