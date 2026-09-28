"use client";

import React, { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import {
  api,
  NextLearningActivity,
  NextLearningRecommendation,
  NextLearningRead,
} from "@/lib/api";
import { Spinner } from "@/components/shared/Spinner";
import { ErrorBanner } from "@/components/shared/ErrorBanner";
import { EmptyState } from "@/components/shared/EmptyState";
import { BookOpen, Compass, Target, Layers, PlayCircle, RefreshCw } from "lucide-react";

const ACTIVITY_META: Record<
  NextLearningActivity,
  { label: string; cta: string; badge: string; bar: string; icon: typeof Target }
> = {
  study: {
    label: "Study",
    cta: "Study materials",
    badge: "bg-sky-950/80 border-sky-800/60 text-sky-400",
    bar: "from-sky-500 to-blue-500",
    icon: BookOpen,
  },
  quiz: {
    label: "Quiz",
    cta: "Take a quiz",
    badge: "bg-amber-950/80 border-amber-800/60 text-amber-400",
    bar: "from-amber-500 to-orange-500",
    icon: Target,
  },
  flashcards: {
    label: "Flashcards",
    cta: "Review flashcards",
    badge: "bg-cyan-950/80 border-cyan-800/60 text-cyan-400",
    bar: "from-cyan-500 to-teal-500",
    icon: Layers,
  },
};

interface NextBestActionProps {
  refreshKey?: number;
}

export function NextBestAction({ refreshKey = 0 }: NextBestActionProps) {
  const router = useRouter();
  const [data, setData] = useState<NextLearningRead | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = async () => {
    setIsLoading(true);
    setError(null);
    try {
      const result = await api.getNextLearning();
      setData(result);
    } catch (err: any) {
      setError(err.message || "Failed to load your next best action.");
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    load();
  }, [refreshKey]);

  const openRecommendation = (rec: NextLearningRecommendation) => {
    const focus =
      rec.activity_type === "study"
        ? "materials"
        : rec.activity_type === "flashcards"
          ? "flashcards"
          : "quiz-engine";
    router.push(
      `/subjects/${rec.subject_id}?topic=${encodeURIComponent(rec.topic)}&focus=${focus}`,
    );
  };

  if (isLoading && !data) {
    return (
      <div className="flex items-center justify-center gap-3 p-10 rounded-2xl border border-slate-800 bg-slate-900/60">
        <Spinner className="h-5 w-5" />
        <p className="text-xs text-slate-400">Choosing your next best step...</p>
      </div>
    );
  }

  if (error) {
    return (
      <div className="space-y-3">
        <ErrorBanner message={error} />
        <button
          onClick={load}
          className="inline-flex items-center gap-2 px-3 py-1.5 rounded-lg border border-slate-700 bg-slate-800/60 text-slate-300 hover:text-white text-xs font-semibold transition-colors"
        >
          <RefreshCw className="h-3.5 w-3.5" /> Retry
        </button>
      </div>
    );
  }

  if (!data || data.status === "insufficient_data" || !data.recommendation) {
    return (
      <div className="rounded-2xl border border-slate-800 bg-slate-900/60 p-6">
        <EmptyState
          icon={<Compass className="h-6 w-6" />}
          title="No next step yet"
          description="Answer a few quiz questions in any subject and the Adaptive Learning Engine will pick the single most valuable topic and activity for you here."
        />
      </div>
    );
  }

  const rec = data.recommendation;
  const meta = ACTIVITY_META[rec.activity_type];
  const ActivityIcon = meta.icon;

  return (
    <div className="rounded-2xl border border-slate-800 bg-slate-900/60 p-4 sm:p-5 space-y-3">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="text-xs font-bold text-white capitalize truncate">{rec.topic}</p>
          <p className="text-[11px] text-slate-500 truncate">{rec.subject_title}</p>
        </div>
        <span
          className={`shrink-0 inline-flex items-center gap-1 px-2.5 py-1 rounded-full border text-[10px] font-semibold ${meta.badge}`}
        >
          <ActivityIcon className="h-3 w-3" /> {meta.label}
        </span>
      </div>

      <div className="flex items-center gap-3">
        <div className="flex-1 space-y-0.5">
          <div className="flex justify-between text-[11px]">
            <span className="text-slate-500">
              Mastery {rec.mastery}% • Accuracy {rec.accuracy}%
            </span>
            <span className="text-slate-500">
              {rec.activity_type === "flashcards" && rec.due_cards > 0
                ? `${rec.due_cards} cards due`
                : rec.last_attempt_at
                  ? "Revisit when you're ready"
                  : "Untouched topic"}
            </span>
          </div>
          <div className="h-1.5 rounded-full bg-slate-800 overflow-hidden">
            <div
              className={`h-full rounded-full bg-gradient-to-r ${meta.bar} transition-all duration-300`}
              style={{ width: `${Math.min(100, rec.priority)}%` }}
            />
          </div>
        </div>
        <span className="shrink-0 text-[11px] text-slate-500 font-semibold">
          Priority {rec.priority}
        </span>
      </div>

      <div className="flex items-center justify-between gap-3">
        <p className="flex items-center gap-1.5 text-[11px] text-slate-400 text-left">
          <Target className="h-3 w-3 text-sky-400 shrink-0" />
          {rec.reason}
        </p>
        <button
          onClick={() => openRecommendation(rec)}
          className="shrink-0 inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-sky-600 hover:bg-sky-500 text-white text-[11px] font-semibold transition-colors"
        >
          <PlayCircle className="h-3.5 w-3.5" /> {meta.cta}
        </button>
      </div>
    </div>
  );
}