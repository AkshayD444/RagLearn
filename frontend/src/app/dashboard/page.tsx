"use client";

import React, { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { useAuthStore } from "@/store/useAuthStore";
import { api, AnalyticsOverview, Subject } from "@/lib/api";
import { AuthModal } from "@/components/auth/AuthModal";
import { SubjectCard } from "@/components/dashboard/SubjectCard";
import { CreateSubjectModal } from "@/components/dashboard/CreateSubjectModal";
import { RecommendedForYou } from "@/components/recommendations/RecommendedForYou";
import { NextBestAction } from "@/components/dashboard/NextBestAction";
import { StudyPlanSection } from "@/components/study-plan/StudyPlanSection";
import { DueTodaySection } from "@/components/dashboard/DueTodaySection";
import { HistorySection } from "@/components/history/HistorySection";
import { Sidebar } from "@/components/Sidebar";
import { BookOpen, Plus, LogOut, Sparkles, CalendarClock, ListChecks, History } from "lucide-react";

export default function DashboardPage() {
  const router = useRouter();
  const { user, isAuthenticated, initialize, logout, isLoading: authLoading } = useAuthStore();
  const [subjects, setSubjects] = useState<Subject[]>([]);
  const [isCreateModalOpen, setIsCreateModalOpen] = useState(false);
  const [isFetchingSubjects, setIsFetchingSubjects] = useState(false);
  const [overview, setOverview] = useState<AnalyticsOverview | null>(null);

  useEffect(() => {
    initialize();
  }, [initialize]);

  useEffect(() => {
    if (isAuthenticated) {
      fetchSubjects();
    }
  }, [isAuthenticated]);

  useEffect(() => {
    if (!isAuthenticated) return;
    let active = true;
    api
      .getAnalyticsOverview()
      .then((data) => {
        if (active) setOverview(data);
      })
      .catch((err) => {
        console.error("Failed to fetch analytics overview:", err);
      });
    return () => {
      active = false;
    };
  }, [isAuthenticated]);

  const fetchSubjects = async () => {
    setIsFetchingSubjects(true);
    try {
      const data = await api.getSubjects();
      setSubjects(data);
    } catch (err) {
      console.error("Failed to fetch subjects:", err);
    } finally {
      setIsFetchingSubjects(false);
    }
  };

  const handleCreateSubject = async (payload: { title: string; description: string; color: string }) => {
    const newSubject = await api.createSubject(payload);
    setSubjects((prev) => [newSubject, ...prev]);
  };

  const handleDeleteSubject = async (id: string) => {
    if (!confirm("Are you sure you want to delete this subject?")) return;
    try {
      await api.deleteSubject(id);
      setSubjects((prev) => prev.filter((s) => s.id !== id));
    } catch (err) {
      console.error("Failed to delete subject:", err);
    }
  };

  if (authLoading) {
    return (
      <div className="min-h-screen bg-slate-950 flex items-center justify-center">
        <div className="flex flex-col items-center gap-3">
          <div className="h-8 w-8 border-4 border-sky-500 border-t-transparent rounded-full animate-spin"></div>
          <p className="text-sm font-mono text-slate-400">Loading AI Assistant workspace...</p>
        </div>
      </div>
    );
  }

  if (!isAuthenticated || !user) {
    return (
      <main className="min-h-screen bg-slate-950 flex items-center justify-center p-4">
        <AuthModal />
      </main>
    );
  }

  return (
    <div className="min-h-screen bg-slate-950 text-slate-100">
      {/* Application Shell */}
      <Sidebar />

      {/* Main Container */}
      <main className="ml-64 sm:ml-80 max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-6 space-y-6">
        {/* Top Section - Compact page heading with welcome and primary action */}
        <section className="flex flex-col sm:flex-row gap-6 items-start sm:items-center">
          <div className="flex-1">
            <div className="flex items-center gap-2 mb-2">
              <h1 className="text-xl font-extrabold text-white tracking-tight">
                Welcome back, {user.full_name.split(" ")[0]}!
              </h1>
            </div>
            <p className="text-slate-400 text-xs sm:text-base">
              {user.email}
            </p>
            <p className="text-slate-400 text-sm sm:text-base leading-relaxed">
              Organize your academic courses into subjects below to begin uploading PDFs, generating grounded RAG study materials, and running adaptive quizzes.
            </p>
          </div>

          <div className="flex items-center gap-3">
            <button
              onClick={() => setIsCreateModalOpen(true)}
              className="inline-flex items-center gap-2 px-4 py-2.5 rounded-xl bg-emerald-600 hover:bg-emerald-500 text-white text-xs font-semibold shadow-sm shadow-emerald-400 transition-all"
            >
              <Plus className="h-4 w-4" /> Add Subject
            </button>
            <button
              onClick={logout}
              title="Logout"
              className="inline-flex items-center gap-2 px-4 py-2.5 rounded-xl bg-rose-600 hover:bg-rose-500 text-white text-xs font-semibold shadow-sm shadow-rose-400 transition-all"
            >
              <LogOut className="h-4 w-4" /> Logout
            </button>
          </div>
        </section>

        {/* Statistics Row - Compact, refined components */}
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-3 mb-4">
          <div className="p-3 rounded-xl border border-slate-700 bg-slate-800/50 flex items-center gap-3">
            <div className="p-2 rounded-lg bg-emerald-500/10 border border-emerald-500/20">
              <BookOpen className="h-4 w-4 text-emerald-400" />
            </div>
            <div>
              <p className="text-xs text-slate-500 font-medium">Enrolled Subjects</p>
              <p className="text-lg font-bold text-white">{subjects.length}</p>
            </div>
          </div>

          <div className="p-3 rounded-xl border border-slate-700 bg-slate-800/50 flex items-center gap-3">
            <div className="p-2 rounded-lg bg-emerald-500/10 border border-emerald-500/20">
              <BookOpen className="h-4 w-4 text-emerald-400" />
            </div>
            <div>
              <p className="text-xs text-slate-500 font-medium">RAG Document Chunks</p>
              <p className="text-lg font-bold text-white">
                {overview ? `${overview.totals.chunk_count} Chunks` : "0 Chunks"}
              </p>
            </div>
          </div>

          <div className="p-3 rounded-xl border border-slate-700 bg-slate-800/50 flex items-center gap-3">
            <div className="p-2 rounded-lg bg-emerald-500/10 border border-emerald-500/20">
              <BookOpen className="h-4 w-4 text-emerald-400" />
            </div>
            <div>
              <p className="text-xs text-slate-500 font-medium">Topic Mastery Score</p>
              <p className="text-lg font-bold text-white">
                {overview ? `${Math.round(overview.totals.average_topic_mastery)}%` : "0%"}
              </p>
            </div>
          </div>
        </div>

        {/* Main Content - Structured dashboard layout */}
        <div className="grid grid-cols-1 gap-6">
          {/* Continue learning & My subjects sidebar-style columns */}
          <div className="grid grid-cols-1 gap-4">
            {/* Next Best Action */}
            <section className="rounded-2xl border border-slate-700 bg-slate-800/60 p-4 sm:p-5">
              <div className="flex items-start justify-between gap-3">
                <div className="flex-1">
                  <h2 className="text-sm font-medium text-slate-400 uppercase tracking-wider mb-1">Next Best Action</h2>
                  <NextBestAction />
                </div>
              </div>
            </section>

            {/* Recent Activity */}
            <section className="rounded-2xl border border-slate-700 bg-slate-800/60 p-4 sm:p-5">
              <div className="flex items-start justify-between gap-3">
                <div>
                  <h2 className="text-sm font-medium text-slate-400 uppercase tracking-wider mb-1">Recent Activity</h2>
                  <p className="text-xs text-slate-500">
                    Everything you have already studied: lifetime totals, your recent activity timeline, and the full quiz and attempt history.
                  </p>
                </div>
              </div>
              <HistorySection onNavigateToSubject={(id) => router.push(`/subjects/${id}`)} />
            </section>

            {/* Due Today */}
            <section className="rounded-2xl border border-slate-700 bg-slate-800/60 p-4 sm:p-5">
              <div className="flex items-start justify-between gap-3">
                <div>
                  <h2 className="text-sm font-medium text-slate-400 uppercase tracking-wider mb-1">Due Today</h2>
                </div>
              </div>
              <DueTodaySection />
            </section>

            {/* Learning Progress */}
            <section className="rounded-2xl border border-slate-700 bg-slate-800/60 p-4 sm:p-5">
              <div className="flex items-start justify-between gap-3">
                <div>
                  <h2 className="text-sm font-medium text-slate-400 uppercase tracking-wider mb-1">Learning Progress</h2>
                  <p className="text-xs text-slate-500">
                    Track your mastery across topics and subjects over time.
                  </p>
                </div>
              </div>
              {/* Progress overview can be rendered here */}
            </section>
          </div>

          {/* My subjects grid */}
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
            {isFetchingSubjects ? (
              <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
                {[1, 2, 3].map((i) => (
                  <div key={i} className="h-48 rounded-2xl bg-slate-800/40 border border-slate-700 animate-pulse" />
                ))}
              </div>
            ) : subjects.length === 0 ? (
              <div className="p-8 rounded-2xl border border-slate-700 bg-slate-800/30 text-center space-y-4">
                <div className="inline-flex p-3 rounded-full bg-slate-700/60 text-slate-400">
                  <BookOpen className="h-6 w-6" />
                </div>
                <div>
                  <h3 className="text-base font-bold text-white">No Subjects Added Yet</h3>
                  <p className="text-sm text-slate-500 mt-1 max-w-sm mx-auto">
                    Click Create Subject above to set up your first course module. Add subjects to organize your courses, upload PDFs, and generate RAG-powered study materials.
                  </p>
                  <button
                    onClick={() => setIsCreateModalOpen(true)}
                    className="inline-flex items-center gap-2 px-4 py-2 rounded-xl bg-emerald-600/20 text-emerald-400 border border-emerald-500/30 text-xs font-semibold hover:bg-emerald-600/30 transition-colors"
                  >
                    <Plus className="h-4 w-4" /> Create First Subject
                  </button>
                </div>
              </div>
            ) : subjects.map((subject) => (
              <SubjectCard
                key={subject.id}
                subject={subject}
                onDelete={handleDeleteSubject}
              />
            ))}
          </div>
        </div>
      </main>
      <CreateSubjectModal
        isOpen={isCreateModalOpen}
        onClose={() => setIsCreateModalOpen(false)}
        onCreate={handleCreateSubject}
      />
    </div>
  );
}