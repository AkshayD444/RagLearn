"use client";

import { useState, useEffect } from "react";
import { useRouter } from "next/navigation";
import { useAuthStore } from "@/store/useAuthStore";
import { api } from "@/lib/api";
import { StudentIntelligence } from "@/components/analytics/StudentIntelligence";
import { Sidebar } from "@/components/Sidebar";

export default function AnalyticsPage() {
  const router = useRouter();
  const { user, isAuthenticated, initialize } = useAuthStore();
  const [subjects, setSubjects] = useState<{ id: string; title: string }[]>([]);
  const [selectedSubjectId, setSelectedSubjectId] = useState<string | null>(null);
  const [overview, setOverview] = useState<any>(null);

  useEffect(() => {
    initialize();
  }, [initialize]);

  useEffect(() => {
    if (isAuthenticated) {
      fetchSubjects();
      fetchOverview();
    }
  }, [isAuthenticated]);

  const fetchSubjects = async () => {
    try {
      const data = await api.getSubjects();
      setSubjects(data.map((s) => ({ id: s.id, title: s.title })));
    } catch (err) {
      console.error("Failed to fetch subjects:", err);
    }
  };

  const fetchOverview = async () => {
    if (!isAuthenticated) return;
    try {
      const data = await api.getAnalyticsOverview();
      setOverview(data);
    } catch (err) {
      console.error("Failed to fetch analytics overview:", err);
    }
  };

  const subjectOptions = subjects.map((s) => (
    <option key={s.id} value={s.id}>
      {s.title}
    </option>
  ));

  if (!isAuthenticated || !user) {
    return (
      <main className="min-h-screen bg-slate-950 flex items-center justify-center p-4">
        <div className="text-center">
          <div className="inline-flex p-3 rounded-full bg-slate-700/60 text-slate-400">
            <svg className="h-6 w-6 text-emerald-400" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <polyline points="2 3 6 3 6 9 10 9 10 13 14 13 14 17 10 17 10 21 6 21 6 3"></polyline>
            </svg>
          </div>
          <h2 className="mt-4 text-2xl font-extrabold text-white">Analytics</h2>
          <p className="mt-2 text-slate-400">View your performance data</p>
        </div>
      </main>
    );
  }

  return (
    <main className="min-h-screen bg-slate-950 text-slate-100 pb-12">
      <header className="sticky top-0 z-40 border-b border-border bg-slate-900/80 backdrop-blur-sm">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 h-16 flex items-center justify-between">
          <div className="flex items-center gap-2">
            <svg
              className="h-3 w-3 rounded-full"
              style={{ backgroundColor: "#10b981" }}
            />
            <span className="font-bold text-sm text-white">Analytics</span>
          </div>
          <div className="flex items-center gap-2">
            <button
              onClick={() => router.push("/dashboard")}
              className="flex items-center gap-1 text-slate-400 hover:text-white text-xs font-semibold transition-colors"
            >
              <svg className="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <line x1="3" y1="6" x2="21" y2="6"></line>
                <line x1="3" y1="12" x2="21" y2="12"></line>
                <line x1="3" y1="18" x2="21" y2="18"></line>
              </svg>
              Back to Dashboard
            </button>
          </div>
        </div>
      </header>

      <main className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8">
        <div className="space-y-6">
          {/* Subject Selector */}
          <div className="rounded-2xl border border-border bg-slate-900/60 p-4 sm:p-5">
            <h2 className="text-sm font-medium text-slate-400 uppercase tracking-wider mb-2">Select Subject</h2>
            <select
              value={selectedSubjectId || ""}
              onChange={(e) => setSelectedSubjectId(e.target.value)}
              className="w-full px-3 py-2 rounded-xl border border-border bg-slate-800/50 text-sm font-medium text-white focus:outline-none focus:border-emerald-500 focus:shadow-sm focus:shadow-emerald-400/20"
            >
              <option value="">Select a subject...</option>
              {subjectOptions}
            </select>
          </div>

          {/* Subject Performance */}
          {selectedSubjectId && overview && (
<StudentIntelligence
              subjectId={selectedSubjectId}
              refreshKey={0}
/>
          )}

          {(!selectedSubjectId || !overview) && (
            <div className="p-8 rounded-2xl border border-slate-700 bg-slate-800/30 text-center space-y-4">
              <svg className="inline-flex h-6 w-6 rounded-full bg-slate-700/60 text-slate-400 mx-auto mb-3">
                <polyline points="2 3 6 3 6 9 10 9 10 13 14 13 14 17 10 17 10 21 6 21 6 3"></polyline>
              </svg>
              <h3 className="text-base font-bold text-white">Select a Subject</h3>
              <p className="text-slate-500 mt-2">
                Choose a subject above to view your learning statistics, including attempt history,
                accuracy, mastery scores, and topic-wise progress.
              </p>
              <p className="text-slate-500 text-sm">
                Analytics provide insights into your performance across all subjects with
                lifetime totals, timelines, and per-topic mastery levels.
              </p>
            </div>
          )}

          {/* Overall Overview */}
          {(!selectedSubjectId || !overview) && overview && (
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
              <div className="p-4 rounded-xl border border-border bg-slate-900/50">
                <p className="text-xs text-slate-400 uppercase tracking-wider mb-1">Total Attempts</p>
                <p className="text-2xl font-bold text-white">{overview.totals.attempt_count}</p>
              </div>
              <div className="p-4 rounded-xl border border-border bg-slate-900/50">
                <p className="text-xs text-slate-400 uppercase tracking-wider mb-1">Quizzes Taken</p>
                <p className="text-2xl font-bold text-white">{overview.totals.quiz_count}</p>
              </div>
              <div className="p-4 rounded-xl border border-border bg-slate-900/50">
                <p className="text-xs text-slate-400 uppercase tracking-wider mb-1">Accuracy</p>
                <p className="text-2xl font-bold text-white">
                  {Math.round(overview.totals.accuracy)}%
                </p>
              </div>
            </div>
          )}
        </div>
      </main>
    </main>
  );
}