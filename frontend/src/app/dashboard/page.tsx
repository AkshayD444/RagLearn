"use client";

import React, { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { useAuthStore } from "@/store/useAuthStore";
import { api, Subject } from "@/lib/api";
import { AuthModal } from "@/components/auth/AuthModal";
import { SubjectCard } from "@/components/dashboard/SubjectCard";
import { CreateSubjectModal } from "@/components/dashboard/CreateSubjectModal";
import { RecommendedForYou } from "@/components/recommendations/RecommendedForYou";
import { StudyPlanSection } from "@/components/study-plan/StudyPlanSection";
import { DueTodaySection } from "@/components/dashboard/DueTodaySection";
import { HistorySection } from "@/components/history/HistorySection";
import { 
  BookOpen, 
  Plus, 
  LogOut, 
  Sparkles, 
  BrainCircuit, 
  BarChart3, 
  Layers, 
  GraduationCap,
  ListChecks,
  CalendarClock,
  History
} from "lucide-react";

export default function DashboardPage() {
  const router = useRouter();
  const { user, token, initialize, logout, isLoading: authLoading } = useAuthStore();
  const [subjects, setSubjects] = useState<Subject[]>([]);
  const [isCreateModalOpen, setIsCreateModalOpen] = useState(false);
  const [isFetchingSubjects, setIsFetchingSubjects] = useState(false);

  useEffect(() => {
    initialize();
  }, [initialize]);

  useEffect(() => {
    if (token) {
      fetchSubjects();
    }
  }, [token]);

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

  if (!token || !user) {
    return (
      <main className="min-h-screen bg-slate-950 flex items-center justify-center p-4">
        <AuthModal />
      </main>
    );
  }

  return (
    <div className="min-h-screen bg-slate-950 text-slate-100">
      {/* Navbar */}
      <header className="sticky top-0 z-40 border-b border-slate-800 bg-slate-900/80 backdrop-blur-xl">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 h-16 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="p-2 rounded-xl bg-gradient-to-tr from-sky-600 to-indigo-600 text-white shadow-md shadow-sky-500/20">
              <GraduationCap className="h-5 w-5" />
            </div>
            <div>
              <span className="font-bold text-lg text-white tracking-tight">AI Learning Assistant</span>
              <span className="hidden sm:inline-block ml-2 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider rounded-md bg-sky-950 text-sky-400 border border-sky-800/60">
                Phase 2 Core
              </span>
            </div>
          </div>

          <div className="flex items-center gap-4">
            <div className="text-right hidden sm:block">
              <p className="text-sm font-semibold text-white">{user.full_name}</p>
              <p className="text-xs text-slate-400">{user.email}</p>
            </div>

            <button
              onClick={logout}
              title="Logout"
              className="flex items-center gap-2 px-3 py-1.5 rounded-xl border border-slate-800 bg-slate-800/60 hover:bg-rose-950/40 hover:border-rose-800/60 text-slate-300 hover:text-rose-300 text-xs font-semibold transition-all"
            >
              <LogOut className="h-3.5 w-3.5" /> Logout
            </button>
          </div>
        </div>
      </header>

      {/* Main Container */}
      <main className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8 space-y-8">
        {/* Banner */}
        <section className="relative overflow-hidden rounded-3xl border border-slate-800 bg-gradient-to-r from-slate-900 via-slate-900 to-sky-950/40 p-6 sm:p-8">
          <div className="relative z-10 max-w-2xl">
            <div className="inline-flex items-center gap-2 px-3 py-1 rounded-full bg-sky-500/10 border border-sky-500/20 text-sky-400 text-xs font-semibold mb-4">
              <Sparkles className="h-3.5 w-3.5" /> Student Dashboard
            </div>
            <h1 className="text-2xl sm:text-4xl font-extrabold text-white tracking-tight mb-2">
              Welcome back, {user.full_name.split(" ")[0]}!
            </h1>
            <p className="text-slate-400 text-sm sm:text-base leading-relaxed">
              Organize your academic courses into subjects below to begin uploading PDFs, generating grounded RAG study materials, and running adaptive quizzes.
            </p>
          </div>
        </section>

        {/* Stats Row */}
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
          <div className="p-5 rounded-2xl border border-slate-800 bg-slate-900/40 flex items-center gap-4">
            <div className="p-3 rounded-xl bg-sky-500/10 text-sky-400 border border-sky-500/20">
              <BookOpen className="h-6 w-6" />
            </div>
            <div>
              <p className="text-xs text-slate-400 font-medium">Enrolled Subjects</p>
              <p className="text-2xl font-bold text-white">{subjects.length}</p>
            </div>
          </div>

          <div className="p-5 rounded-2xl border border-slate-800 bg-slate-900/40 flex items-center gap-4">
            <div className="p-3 rounded-xl bg-purple-500/10 text-purple-400 border border-purple-500/20">
              <BrainCircuit className="h-6 w-6" />
            </div>
            <div>
              <p className="text-xs text-slate-400 font-medium">RAG Document Chunks</p>
              <p className="text-2xl font-bold text-white">0 Chunks</p>
            </div>
          </div>

          <div className="p-5 rounded-2xl border border-slate-800 bg-slate-900/40 flex items-center gap-4">
            <div className="p-3 rounded-xl bg-emerald-500/10 text-emerald-400 border border-emerald-500/20">
              <BarChart3 className="h-6 w-6" />
            </div>
            <div>
              <p className="text-xs text-slate-400 font-medium">Topic Mastery Score</p>
              <p className="text-2xl font-bold text-white">0%</p>
            </div>
          </div>
        </div>

        {/* Recommended for You */}
        <section className="space-y-4">
          <div>
            <h2 className="text-xl font-bold text-white flex items-center gap-2">
              <CalendarClock className="h-5 w-5 text-amber-400" /> Due Today
            </h2>
            <p className="text-xs text-slate-400">
              Cards the scheduler has brought back for you, most overdue first. Streak and interval are tracked per card.
            </p>
          </div>
          <DueTodaySection />
        </section>

        <section className="space-y-4">
          <div>
            <h2 className="text-xl font-bold text-white flex items-center gap-2">
              <Sparkles className="h-5 w-5 text-sky-400" /> Recommended for You
            </h2>
            <p className="text-xs text-slate-400">
              Deterministic, data-driven topics to focus on next based on your quiz performance and topic mastery.
            </p>
          </div>
          <RecommendedForYou limit={5} />
        </section>

        <section className="space-y-4">
          <div>
            <h2 className="text-xl font-bold text-white flex items-center gap-2">
              <ListChecks className="h-5 w-5 text-emerald-400" /> Your Study Plan
            </h2>
            <p className="text-xs text-slate-400">
              A personalized sequence built from your measured mastery, accuracy, attempts, and adaptive recommendations.
            </p>
          </div>
          <StudyPlanSection subjects={subjects} />
        </section>

        <section className="space-y-4">
          <div>
            <h2 className="text-xl font-bold text-white flex items-center gap-2">
              <History className="h-5 w-5 text-sky-400" /> Your Learning History
            </h2>
            <p className="text-xs text-slate-400">
              Everything you have already studied: lifetime totals, your recent activity timeline, and the full
              quiz and attempt history.
            </p>
          </div>
          <HistorySection onNavigateToSubject={(id) => router.push(`/subjects/${id}`)} />
        </section>

        {/* Subjects Header & List */}
        <section className="space-y-6">
          <div className="flex items-center justify-between">
            <div>
              <h2 className="text-xl font-bold text-white flex items-center gap-2">
                <Layers className="h-5 w-5 text-sky-400" /> My Subjects
              </h2>
              <p className="text-xs text-slate-400">Manage course modules and active study materials</p>
            </div>

            <button
              onClick={() => setIsCreateModalOpen(true)}
              className="inline-flex items-center gap-2 px-4 py-2.5 rounded-xl bg-sky-600 hover:bg-sky-500 text-white text-xs font-semibold shadow-lg shadow-sky-600/30 transition-all"
            >
              <Plus className="h-4 w-4" /> Add Subject
            </button>
          </div>

          {isFetchingSubjects ? (
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
              {[1, 2, 3].map((i) => (
                <div key={i} className="h-48 rounded-2xl bg-slate-900/40 border border-slate-800 animate-pulse"></div>
              ))}
            </div>
          ) : subjects.length === 0 ? (
            <div className="p-12 rounded-3xl border border-dashed border-slate-800 bg-slate-900/20 text-center space-y-4">
              <div className="inline-flex p-4 rounded-full bg-slate-800/60 text-slate-400">
                <BookOpen className="h-8 w-8" />
              </div>
              <div>
                <h3 className="text-base font-bold text-white">No Subjects Added Yet</h3>
                <p className="text-xs text-slate-400 mt-1 max-w-sm mx-auto">
                  Click &quot;Add Subject&quot; above to create your first course module (e.g. Computer Science, Physics, History).
                </p>
              </div>
              <button
                onClick={() => setIsCreateModalOpen(true)}
                className="inline-flex items-center gap-2 px-4 py-2 rounded-xl bg-sky-600/20 text-sky-400 border border-sky-600/30 text-xs font-semibold hover:bg-sky-600/30 transition-colors"
              >
                <Plus className="h-4 w-4" /> Create First Subject
              </button>
            </div>
          ) : (
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
              {subjects.map((subject) => (
                <SubjectCard
                  key={subject.id}
                  subject={subject}
                  onDelete={handleDeleteSubject}
                />
              ))}
            </div>
          )}
        </section>
      </main>

      {/* Modal */}
      <CreateSubjectModal
        isOpen={isCreateModalOpen}
        onClose={() => setIsCreateModalOpen(false)}
        onCreate={handleCreateSubject}
      />
    </div>
  );
}
