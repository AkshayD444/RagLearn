"use client";

import { useState, useEffect } from "react";
import { useRouter } from "next/navigation";
import { useAuthStore } from "@/store/useAuthStore";
import { api, Subject } from "@/lib/api";
import { StudyPlanSection } from "@/components/study-plan/StudyPlanSection";
import { Sidebar } from "@/components/Sidebar";

export default function StudyPlansPage() {
  const router = useRouter();
  const { user, isAuthenticated, initialize } = useAuthStore();
  const [subjects, setSubjects] = useState<Subject[]>([]);
  const [selectedSubjectId, setSelectedSubjectId] = useState<string | null>(null);

  useEffect(() => {
    initialize();
  }, [initialize]);

  useEffect(() => {
    if (isAuthenticated) {
      fetchSubjects();
    }
  }, [isAuthenticated]);

  const fetchSubjects = async () => {
    try {
      const data = await api.getSubjects();
      setSubjects(data);
    } catch (err) {
      console.error("Failed to fetch subjects:", err);
    }
  };

  if (!isAuthenticated || !user) {
    return (
      <main className="min-h-screen bg-slate-950 flex items-center justify-center p-4">
        <div className="text-center">
          <div className="inline-flex p-3 rounded-full bg-slate-700/60 text-slate-400">
            <svg className="h-6 w-6" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <circle cx="12" cy="12" r="10"></circle>
              <polyline points="10 8 16 12 10 16"></polyline>
            </svg>
          </div>
          <h2 className="mt-4 text-2xl font-extrabold text-white">Study Plans</h2>
          <p className="mt-2 text-slate-400">Personalized learning sequences</p>
        </div>
      </main>
    );
  }

  return (
    <main className="min-h-screen bg-slate-950 text-slate-100 pb-12">
      <header className="sticky top-0 z-40 border-b border-border bg-slate-900/80 backdrop-blur-sm">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 h-16 flex items-center justify-between">
          <div className="flex items-center gap-2">
            <svg className="h-3 w-3 rounded-full" style={{ backgroundColor: "#10b981" }} />
            <span className="font-bold text-sm text-white">Study Plans</span>
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
          {/* Study Plan Section */}
          {subjects.length > 0 && (
            <StudyPlanSection
              subjects={subjects}
            />
          )}

          {subjects.length === 0 && (
            <div className="p-8 rounded-2xl border border-slate-700 bg-slate-800/30 text-center space-y-4">
              <svg className="inline-flex h-6 w-6 rounded-full bg-slate-700/60 text-slate-400 mx-auto mb-3">
                <circle cx="12" cy="12" r="10"></circle>
                <polyline points="10 8 16 12 10 16"></polyline>
              </svg>
              <h3 className="text-base font-bold text-white">No Subject Selected</h3>
              <p className="text-slate-500 mt-2">
                Select a subject above to generate a personalized study plan based on your
                learning history and mastery data.
              </p>
              <p className="text-slate-500 text-sm">
                Study plans are generated from your quiz performance, flashcard progress, and
                topic mastery across your subjects.
              </p>
            </div>
          )}
        </div>
      </main>
    </main>
  );
}