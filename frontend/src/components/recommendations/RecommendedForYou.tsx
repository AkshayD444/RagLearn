"use client";

import React, { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { api, Recommendation, RecommendationType } from "@/lib/api";
import { Spinner } from "@/components/shared/Spinner";
import { ErrorBanner } from "@/components/shared/ErrorBanner";
import { EmptyState } from "@/components/shared/EmptyState";
import { Compass, Target, PlayCircle } from "lucide-react";

const TYPE_STYLES: Record<RecommendationType, string> = {
  REVIEW: "bg-rose-950/80 border-rose-800/60 text-rose-400",
  PRACTICE: "bg-amber-950/80 border-amber-800/60 text-amber-400",
  MAINTAIN: "bg-emerald-950/80 border-emerald-800/60 text-emerald-400",
};

const TYPE_BAR_STYLES: Record<RecommendationType, string> = {
  REVIEW: "from-rose-500 to-red-500",
  PRACTICE: "from-amber-500 to-orange-500",
  MAINTAIN: "from-emerald-500 to-teal-500",
};

interface RecommendedForYouProps {
  limit?: number;
  refreshKey?: number;
}

export function RecommendedForYou({ limit = 5, refreshKey = 0 }: RecommendedForYouProps) {
  const router = useRouter();
  const [recommendations, setRecommendations] = useState<Recommendation[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = async () => {
    setIsLoading(true);
    setError(null);
    try {
      const data = await api.getTopRecommendations(limit);
      setRecommendations(data);
    } catch (err: any) {
      setError(err.message || "Failed to load recommendations.");
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    load();
  }, [limit, refreshKey]);

  const openPractice = (rec: Recommendation) => {
    router.push(`/subjects/${rec.subject_id}?topic=${encodeURIComponent(rec.topic)}`);
  };

  if (isLoading && recommendations.length === 0) {
    return (
      <div className="flex items-center justify-center gap-3 p-10 rounded-2xl border border-slate-800 bg-slate-900/60">
        <Spinner className="h-5 w-5" />
        <p className="text-xs text-slate-400">Computing adaptive recommendations...</p>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      {error && <ErrorBanner message={error} />}

      {!isLoading && recommendations.length === 0 && (
        <div className="rounded-2xl border border-slate-800 bg-slate-900/60 p-6">
          <EmptyState
            icon={<Compass className="h-6 w-6" />}
            title="No recommendations yet"
            description="Complete a few quizzes in any subject. The Adaptive Learning Engine will then suggest which topics to focus on next."
          />
        </div>
      )}

      {recommendations.map((rec) => (
        <div
          key={`${rec.subject_id}:${rec.topic}`}
          className="rounded-2xl border border-slate-800 bg-slate-900/60 p-4 sm:p-5 space-y-3"
        >
          <div className="flex items-start justify-between gap-3">
            <div className="min-w-0">
              <p className="text-xs font-bold text-white capitalize truncate">{rec.topic}</p>
              <p className="text-[11px] text-slate-500 truncate">{rec.subject_title}</p>
            </div>
            <span
              className={`shrink-0 inline-flex items-center px-2.5 py-1 rounded-full border text-[10px] font-semibold ${TYPE_STYLES[rec.recommendation_type]}`}
            >
              {rec.recommendation_type}
            </span>
          </div>

          <div className="flex items-center gap-3">
            <div className="flex-1 space-y-0.5">
              <div className="flex justify-between text-[11px]">
                <span className="text-slate-500">
                  Mastery {rec.mastery}% • Accuracy {rec.accuracy}%
                </span>
                <span className="text-slate-500">
                  {rec.questions_attempted} questions • {rec.questions_incorrect} missed
                </span>
              </div>
              <div className="h-1.5 rounded-full bg-slate-800 overflow-hidden">
                <div
                  className={`h-full rounded-full bg-gradient-to-r ${TYPE_BAR_STYLES[rec.recommendation_type]} transition-all duration-300`}
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
              onClick={() => openPractice(rec)}
              title={rec.suggested_action}
              className="shrink-0 inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-sky-600 hover:bg-sky-500 text-white text-[11px] font-semibold transition-colors"
            >
              <PlayCircle className="h-3.5 w-3.5" /> Practice
            </button>
          </div>
        </div>
      ))}
    </div>
  );
}